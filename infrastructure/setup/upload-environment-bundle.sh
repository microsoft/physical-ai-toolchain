#!/usr/bin/env bash
# Upload a generated non-secret environment bundle to Azure Key Vault.
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

Upload generated deployment metadata and non-secret manifests to Azure Key Vault.
Kubeconfigs, OSMO profiles, tokens, registry credentials, and Terraform state are rejected by the fixed artifact allowlist.

OPTIONS:
    -h, --help                Show this help message
    -e, --environment NAME    Environment bundle name (required)
    -t, --tf-dir DIR          Terraform directory (default: $DEFAULT_TF_DIR)
    --bundle-dir DIR          Bundle directory (default: generated/<environment>)
    --vault-name NAME         Key Vault name (default: Terraform output)
    --config-preview          Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001
    $(basename "$0") --environment dev-001 --bundle-dir generated/dev-001
EOF
}

# Defaults
environment=""
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
bundle_dir=""
vault_name=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    -e|--environment)   environment="$2"; shift 2 ;;
    -t|--tf-dir)        tf_dir="$2"; shift 2 ;;
    --bundle-dir)       bundle_dir="$2"; shift 2 ;;
    --vault-name)       vault_name="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform jq

[[ -n "$environment" ]] || fatal "--environment is required"
[[ "$environment" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || \
  fatal "Environment must use lowercase letters, numbers, and internal hyphens"
bundle_dir="${bundle_dir:-$SCRIPT_DIR/generated/$environment}"
[[ -d "$bundle_dir" && ! -L "$bundle_dir" ]] || fatal "Bundle directory not found or is a symlink: $bundle_dir"

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading Terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")
terraform_vault=$(tf_require "$tf_output" "key_vault_name.value" "Key Vault name")
terraform_resource_group=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
terraform_aks_cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster")
terraform_aks_resource_id=$(tf_require "$tf_output" "aks_cluster.value.id" "AKS resource ID")
vault_name="${vault_name:-$terraform_vault}"
[[ "$vault_name" == "$terraform_vault" ]] || fatal "--vault-name does not match the Terraform Key Vault output"

az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
subscription_id=$(az account show --query id -o tsv)
deployment_file="$bundle_dir/deployment.json"
[[ -f "$deployment_file" && ! -L "$deployment_file" ]] || fatal "Required deployment metadata not found: $deployment_file"

jq -e --arg environment "$environment" --arg vault "$vault_name" --arg subscription "$subscription_id" \
  --arg resource_group "$terraform_resource_group" --arg aks_cluster "$terraform_aks_cluster" \
  --arg aks_resource_id "$terraform_aks_resource_id" '
  .schema_version == 1 and
  .environment == $environment and
  .key_vault_name == $vault and
  .subscription_id == $subscription and
  .resource_group == $resource_group and
  .aks_cluster == $aks_cluster and
  ((.aks_resource_id | ascii_downcase) == ($aks_resource_id | ascii_downcase)) and
  (.osmo_service_url | type == "string" and test("^https?://[^[:space:]]+$")) and
  (.artifacts | type == "object") and
  ((.artifacts | keys) - ["osmo_platforms", "osmo_images", "azureml_instance_types"] | length == 0)
' "$deployment_file" >/dev/null || fatal "deployment.json does not match Terraform and the selected Azure environment"

live_aks_resource_id=$(az aks show --resource-group "$terraform_resource_group" \
  --name "$terraform_aks_cluster" --query id -o tsv)
[[ "$(printf '%s' "$live_aks_resource_id" | tr '[:upper:]' '[:lower:]')" == \
  "$(printf '%s' "$terraform_aks_resource_id" | tr '[:upper:]' '[:lower:]')" ]] || \
  fatal "Live AKS resource does not match Terraform"

artifact_entries=(
  "deployment|deployment.json|application/json|required"
  "osmo-platforms|osmo-platforms.yaml|application/yaml|optional"
  "osmo-images|osmo-images.json|application/json|optional"
  "azureml-instance-types|azureml-instance-types.yaml|application/yaml|optional"
)
upload_entries=()

for entry in "${artifact_entries[@]}"; do
  IFS='|' read -r artifact file_name content_type requirement <<< "$entry"
  file="$bundle_dir/$file_name"
  if [[ "$requirement" == "optional" ]]; then
    metadata_key="${artifact//-/_}"
    metadata_file=$(jq -r --arg key "$metadata_key" '.artifacts[$key].file // empty' "$deployment_file")
    if [[ -z "$metadata_file" ]]; then
      [[ ! -e "$file" ]] || fatal "$file_name exists but is omitted from deployment.json"
      continue
    fi
    [[ "$metadata_file" == "$file_name" ]] || fatal "Invalid artifact path in deployment.json: $metadata_file"
  fi
  if [[ ! -e "$file" ]]; then
    fatal "Required bundle artifact not found: $file"
  fi
  [[ -f "$file" && ! -L "$file" ]] || fatal "Bundle artifact must be a regular non-symlink file: $file"
  [[ -s "$file" ]] || fatal "Bundle artifact is empty: $file"
  file_size=$(wc -c < "$file" | tr -d ' ')
  (( file_size <= 24000 )) || fatal "Bundle artifact exceeds the 24,000-byte Key Vault limit: $file"
  if grep -Eqi -- 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|client-certificate-data:|client-key-data:|bearer[[:space:]]+[A-Za-z0-9._~-]+|^[[:space:]]*(token|password):|"(auths|token|password)"[[:space:]]*:|/(Users|home)/[^/[:space:]]+' "$file"; then
    fatal "Bundle artifact contains credential-like content: $file"
  fi
  upload_entries+=("$artifact|$file_name|$content_type|required")
done

for entry in "${upload_entries[@]}"; do
  IFS='|' read -r artifact file_name _ _ <<< "$entry"
  [[ "$artifact" == "deployment" ]] && continue
  metadata_key="${artifact//-/_}"
  expected_sha=$(jq -r --arg key "$metadata_key" --arg file "$file_name" '
    .artifacts[$key] | select(.file == $file) | .sha256 // empty
  ' "$deployment_file")
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fatal "deployment.json has no valid digest for $file_name"
  actual_sha=$(calculate_sha256 "$bundle_dir/$file_name")
  [[ "$actual_sha" == "$expected_sha" ]] || fatal "Bundle digest mismatch for $file_name"
done

if [[ -f "$bundle_dir/osmo-images.json" ]]; then
  jq -e '
    .schema_version == 1 and
    (.registry | type == "string" and length > 0) and
    (.login_server | type == "string" and length > 0) and
    (.image_version | type == "string" and length > 0) and
    ((.images | keys | sort) == (["agent", "backend-listener", "backend-worker", "client", "delayed-job-monitor", "init-container", "logger", "router", "service", "web-ui", "worker"] | sort)) and
    all(.images | to_entries[]; .value.repository == ("osmo/" + .key) and (.value.digest | test("^sha256:[0-9a-f]{64}$")))
  ' "$bundle_dir/osmo-images.json" >/dev/null || fatal "Invalid OSMO image manifest"
  expected_login_server=$(jq -r '.acr_login_server' "$deployment_file")
  expected_image_version=$(jq -r '.osmo_image_version' "$deployment_file")
  verify_acr_image_manifest "$bundle_dir/osmo-images.json" "$expected_login_server" "$expected_image_version"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Environment" "$environment"
  print_kv "Bundle" "$bundle_dir"
  print_kv "Key Vault" "$vault_name"
  for entry in "${upload_entries[@]}"; do
    IFS='|' read -r artifact file_name _ _ <<< "$entry"
    print_kv "Artifact" "$artifact ($file_name)"
  done
  exit 0
fi

#------------------------------------------------------------------------------
# Upload Bundle
#------------------------------------------------------------------------------

section "Upload Environment Bundle"
for upload_phase in artifacts deployment; do
  for entry in "${upload_entries[@]}"; do
    IFS='|' read -r artifact file_name content_type _ <<< "$entry"
    [[ "$upload_phase" == "artifacts" && "$artifact" == "deployment" ]] && continue
    [[ "$upload_phase" == "deployment" && "$artifact" != "deployment" ]] && continue
    secret_name="${environment}-${artifact}"
    info "Uploading $file_name as Key Vault secret $secret_name..."
    az keyvault secret set \
      --vault-name "$vault_name" \
      --name "$secret_name" \
      --file "$bundle_dir/$file_name" \
      --encoding utf-8 \
      --content-type "$content_type" \
      --tags "physical-ai-environment=$environment" "physical-ai-artifact=$artifact" \
      --only-show-errors \
      --output none
  done
done

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Environment" "$environment"
print_kv "Key Vault" "$vault_name"
print_kv "Artifacts" "${#upload_entries[@]}"
info "Environment bundle upload complete"
