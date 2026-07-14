#!/usr/bin/env bash
# Download a non-secret environment bundle from Azure Key Vault to a protected directory.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME [OPTIONS]

Download deployment metadata and non-secret manifests from Azure Key Vault.
The output directory is created with mode 0700 and downloaded files use mode 0600.

OPTIONS:
    -h, --help                  Show this help message
    -e, --environment NAME      Environment bundle name (required)
    -g, --resource-group NAME   Resource group used to discover one Key Vault
    --vault-name NAME           Key Vault name; required when resource group has multiple vaults
    --subscription ID           Azure subscription containing the Key Vault
    --output-dir DIR            Protected output directory
                                (default: ~/.config/physical-ai-toolchain/environments/<environment>)
    --config-preview            Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001 --resource-group rg-physical-ai-dev-001
    $(basename "$0") --environment dev-001 --vault-name kvphysicalaidev001
EOF
}

# Defaults
environment=""
resource_group=""
vault_name=""
subscription_id=""
output_dir=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    -e|--environment)   environment="$2"; shift 2 ;;
    -g|--resource-group) resource_group="$2"; shift 2 ;;
    --vault-name)       vault_name="$2"; shift 2 ;;
    --subscription)     subscription_id="$2"; shift 2 ;;
    --output-dir)       output_dir="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq

[[ -n "$environment" ]] || fatal "--environment is required"
[[ "$environment" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || \
  fatal "Environment must use lowercase letters, numbers, and internal hyphens"
[[ -n "$resource_group" || -n "$vault_name" ]] || fatal "--resource-group or --vault-name is required"
output_dir="${output_dir:-$HOME/.config/physical-ai-toolchain/environments/$environment}"

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
if [[ -n "$subscription_id" ]]; then
  subscription_id=$(az account show --subscription "$subscription_id" --query id -o tsv)
else
  subscription_id=$(az account show --query id -o tsv)
fi

if [[ -z "$vault_name" ]]; then
  vault_names=()
  while IFS= read -r discovered_vault; do
    [[ -n "$discovered_vault" ]] && vault_names+=("$discovered_vault")
  done < <(az keyvault list --subscription "$subscription_id" \
    --resource-group "$resource_group" --query '[].name' -o tsv)
  (( ${#vault_names[@]} == 1 )) || \
    fatal "Resource group must contain exactly one Key Vault; pass --vault-name when it contains ${#vault_names[@]}"
  vault_name="${vault_names[0]}"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Environment" "$environment"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "${resource_group:-validated after download}"
  print_kv "Key Vault" "$vault_name"
  print_kv "Output" "$output_dir"
  exit 0
fi

require_no_symlink_path "$output_dir"
[[ ! -L "$output_dir" ]] || fatal "Output directory must not be a symlink: $output_dir"
if [[ -d "$output_dir" ]]; then
  require_protected_directory "$output_dir"
else
  mkdir -p "$(dirname "$output_dir")"
fi
umask 077

staging_dir=$(mktemp -d "${output_dir}.tmp.XXXXXX")
chmod 0700 "$staging_dir"
backup_dir=""
cleanup_download() {
  [[ -z "${staging_dir:-}" ]] || rm -rf "$staging_dir"
  if [[ -n "${backup_dir:-}" ]]; then
    if [[ -e "$output_dir" ]]; then
      rm -rf "$backup_dir"
    else
      mv "$backup_dir" "$output_dir"
    fi
  fi
}
trap cleanup_download EXIT

artifact_entries=(
  "osmo-platforms|osmo_platforms|osmo-platforms.yaml"
  "osmo-images|osmo_images|osmo-images.json"
  "azureml-instance-types|azureml_instance_types|azureml-instance-types.yaml"
)
downloaded=0

#------------------------------------------------------------------------------
# Download Bundle
#------------------------------------------------------------------------------

section "Download Environment Bundle"
deployment_file="$staging_dir/deployment.json"
az keyvault secret download \
  --subscription "$subscription_id" \
  --vault-name "$vault_name" \
  --name "${environment}-deployment" \
  --file "$deployment_file" \
  --encoding utf-8 \
  --overwrite \
  --only-show-errors \
  --output none
chmod 0600 "$deployment_file"
downloaded=1

deployment_size=$(wc -c < "$deployment_file" | tr -d ' ')
(( deployment_size <= 24000 )) || fatal "Downloaded deployment metadata exceeds the 24,000-byte bundle limit"
if grep -Eqi -- 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|client-certificate-data:|client-key-data:|bearer[[:space:]]+[A-Za-z0-9._~-]+|^[[:space:]]*(token|password):|"(auths|token|password)"[[:space:]]*:|/(Users|home)/[^/[:space:]]+' "$deployment_file"; then
  fatal "Downloaded deployment metadata contains credential-like content"
fi

#------------------------------------------------------------------------------
# Validate Bundle
#------------------------------------------------------------------------------

jq -e --arg environment "$environment" --arg vault "$vault_name" --arg subscription "$subscription_id" \
  --arg resource_group "$resource_group" '
  .schema_version == 1 and
  .environment == $environment and
  .key_vault_name == $vault and
  .subscription_id == $subscription and
  ($resource_group == "" or .resource_group == $resource_group) and
  (.resource_group | type == "string" and length > 0) and
  (.aks_cluster | type == "string" and length > 0) and
  (.aks_resource_id | type == "string" and length > 0) and
  (.osmo_service_url | type == "string" and test("^https?://[^[:space:]]+$")) and
  (.artifacts | type == "object") and
  ((.artifacts | keys) - ["osmo_platforms", "osmo_images", "azureml_instance_types"] | length == 0)
' "$deployment_file" >/dev/null || fatal "Downloaded deployment metadata does not match the selected Azure environment"

for entry in "${artifact_entries[@]}"; do
  IFS='|' read -r artifact metadata_key file_name <<< "$entry"
  metadata_file=$(jq -r --arg key "$metadata_key" '.artifacts[$key].file // empty' "$deployment_file")
  [[ -n "$metadata_file" ]] || continue
  [[ "$metadata_file" == "$file_name" ]] || fatal "Invalid artifact path in deployment.json: $metadata_file"

  secret_name="${environment}-${artifact}"
  target="$staging_dir/$file_name"
  az keyvault secret download \
    --subscription "$subscription_id" \
    --vault-name "$vault_name" \
    --name "$secret_name" \
    --file "$target" \
    --encoding utf-8 \
    --overwrite \
    --only-show-errors \
    --output none
  chmod 0600 "$target"
  downloaded=$((downloaded + 1))

  expected_sha=$(jq -r --arg key "$metadata_key" --arg file "$file_name" '
    .artifacts[$key] | select(.file == $file) | .sha256 // empty
  ' "$deployment_file")
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fatal "deployment.json has no valid digest for $file_name"
  actual_sha=$(calculate_sha256 "$target")
  [[ "$actual_sha" == "$expected_sha" ]] || fatal "Downloaded bundle digest mismatch for $file_name"
  file_size=$(wc -c < "$target" | tr -d ' ')
  (( file_size <= 24000 )) || fatal "Downloaded artifact exceeds the 24,000-byte bundle limit: $file_name"
  if grep -Eqi -- 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|client-certificate-data:|client-key-data:|bearer[[:space:]]+[A-Za-z0-9._~-]+|^[[:space:]]*(token|password):|"(auths|token|password)"[[:space:]]*:|/(Users|home)/[^/[:space:]]+' "$target"; then
    fatal "Downloaded artifact contains credential-like content: $file_name"
  fi
  info "Downloaded and verified $file_name"
done

if [[ -f "$staging_dir/osmo-images.json" ]]; then
  jq -e '
    .schema_version == 1 and
    (.registry | type == "string" and length > 0) and
    (.login_server | type == "string" and length > 0) and
    (.image_version | type == "string" and length > 0) and
    ((.images | keys | sort) == (["agent", "backend-listener", "backend-worker", "client", "delayed-job-monitor", "init-container", "logger", "router", "service", "web-ui", "worker"] | sort)) and
    all(.images | to_entries[]; .value.repository == ("osmo/" + .key) and (.value.digest | test("^sha256:[0-9a-f]{64}$")))
  ' "$staging_dir/osmo-images.json" >/dev/null || fatal "Downloaded OSMO image manifest is invalid"
  expected_registry=$(jq -r '.acr_name' "$deployment_file")
  expected_login_server=$(jq -r '.acr_login_server' "$deployment_file")
  expected_image_version=$(jq -r '.osmo_image_version' "$deployment_file")
  jq -e --arg registry "$expected_registry" --arg login_server "$expected_login_server" \
    --arg image_version "$expected_image_version" '
    .registry == $registry and .login_server == $login_server and .image_version == $image_version
  ' "$staging_dir/osmo-images.json" >/dev/null || \
    fatal "Downloaded OSMO image manifest does not match deployment metadata"
fi

if [[ -d "$output_dir" ]]; then
  backup_dir=$(mktemp -d "${output_dir}.backup.XXXXXX")
  rmdir "$backup_dir"
  mv "$output_dir" "$backup_dir"
fi
if mv "$staging_dir" "$output_dir"; then
  staging_dir=""
  [[ -z "$backup_dir" ]] || rm -rf "$backup_dir"
  backup_dir=""
else
  [[ -z "$backup_dir" ]] || mv "$backup_dir" "$output_dir"
  backup_dir=""
  fatal "Unable to install the validated environment bundle"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Environment" "$environment"
print_kv "Resource Group" "$(jq -r '.resource_group' "$output_dir/deployment.json")"
print_kv "Key Vault" "$vault_name"
print_kv "Output" "$output_dir"
print_kv "Artifacts" "$downloaded"
info "Environment bundle download complete"
