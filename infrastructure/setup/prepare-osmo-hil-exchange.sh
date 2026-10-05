#!/usr/bin/env bash
# Prepare the Key Vault secrets, registry pull credential, and optional roles that one OSMO HiL host needs.
# cspell:ignore fromdateiso
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

SECRETS_USER_ROLE="Key Vault Secrets User"
SECRETS_OFFICER_ROLE="Key Vault Secrets Officer"

show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME [OPTIONS]

Prepare what 04-prepare-osmo-hil-node.sh expects for one OSMO HiL host:
  - Create the host's missing Key Vault exchange secrets with a placeholder value.
    The catalog is left to 04, which rejects a catalog it didn't write.
  - With --registry-config-file, create a pull token on the registry named in the
    bundle's image manifest and write a protected Docker config for 04.
  - With --assignee-object-id, grant the host identity Key Vault roles on each
    exchange secret only. Rerun after 04 publishes the catalog to grant its role.

Run upload-environment-bundle.sh first; the bundle must include osmo-images.json.
Hosts that only run Azure ML jobs don't need this script.

OPTIONS:
    -h, --help                       Show this help message
    -e, --environment NAME           Environment bundle name (required)
    --host-name NAME                 HiL host name (required)
    -t, --tf-dir DIR                 Terraform directory (default: $DEFAULT_TF_DIR)
    --vault-name NAME                Key Vault (default: Terraform output)
    --with-vpn                       Also prepare the VPN secrets and the CSR role
    --registry-config-file PATH      Protected Docker config to write for the pull token
    --token-expiry YYYY-MM-DD        Pull-token password expiry (required with --registry-config-file)
    --registry-token-name NAME       Pull token name (default: <host>-osmo-pull)
    --scope-map NAME                 Token scope map (default: _repositories_pull)
    --renew-registry-password        Regenerate the token password and rewrite the config
    --assignee-object-id ID          Host identity to grant per-secret roles
    --assignee-principal-type TYPE   User, Group, or ServicePrincipal (default: User)
    --config-preview                 Print configuration and exit

The default scope map pulls from every repository in the registry, because HiL
workflow pods pull their own images with the same credential. Pass a custom scope
map to narrow it.

EXAMPLES:
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \\
      --registry-config-file /protected/hil-lab-01/registry.json --token-expiry 2026-08-01
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \\
      --assignee-object-id <object-id> --assignee-principal-type ServicePrincipal
EOF
}

# Defaults
environment=""
host_name=""
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
vault_name=""
with_vpn=false
registry_config_file=""
token_expiry=""
token_name=""
scope_map="_repositories_pull"
renew_password=false
assignee_id=""
assignee_type="User"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    -e|--environment)             environment="$2"; shift 2 ;;
    --host-name)                  host_name="$2"; shift 2 ;;
    -t|--tf-dir)                  tf_dir="$2"; shift 2 ;;
    --vault-name)                 vault_name="$2"; shift 2 ;;
    --with-vpn)                   with_vpn=true; shift ;;
    --registry-config-file)       registry_config_file="$2"; shift 2 ;;
    --token-expiry)               token_expiry="$2"; shift 2 ;;
    --registry-token-name)        token_name="$2"; shift 2 ;;
    --scope-map)                  scope_map="$2"; shift 2 ;;
    --renew-registry-password)    renew_password=true; shift ;;
    --assignee-object-id)         assignee_id="$2"; shift 2 ;;
    --assignee-principal-type)    assignee_type="$2"; shift 2 ;;
    --config-preview)             config_preview=true; shift ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$environment" ]] || fatal "--environment is required"
[[ -n "$host_name" ]] || fatal "--host-name is required"
hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
token_name="${token_name:-${host_name}-osmo-pull}"
if [[ -n "$registry_config_file" ]]; then
  [[ "$token_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9-]{4,49}$ ]] || \
    fatal "Registry token name must be 5-50 letters, numbers, or hyphens; pass --registry-token-name"
  [[ -n "$scope_map" ]] || fatal "--scope-map must not be empty"
  [[ "$token_expiry" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || \
    fatal "--token-expiry YYYY-MM-DD is required with --registry-config-file"
  token_expiry_utc="${token_expiry}T23:59:59Z"
  jq -n -e --arg expiry "$token_expiry_utc" '$expiry | fromdateiso8601 > now' >/dev/null || \
    fatal "--token-expiry must be a valid future date"
  require_external_runtime_path "$registry_config_file"
else
  [[ -z "$token_expiry" && "$renew_password" == "false" ]] || \
    fatal "--token-expiry and --renew-registry-password need --registry-config-file"
fi
if [[ -n "$assignee_id" ]]; then
  [[ "$assignee_id" =~ ^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$ ]] || \
    fatal "--assignee-object-id must be an object ID (GUID)"
  [[ "$assignee_type" =~ ^(User|Group|ServicePrincipal)$ ]] || \
    fatal "--assignee-principal-type must be User, Group, or ServicePrincipal"
fi

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

if [[ -z "$vault_name" ]]; then
  require_tools terraform jq
  tf_output=$(read_terraform_outputs "$tf_dir")
  vault_name=$(tf_require "$tf_output" "key_vault_name.value" "Key Vault name")
fi

prefix="${environment}-${host_name}"
catalog_secret="${prefix}-hil-catalog"
csr_secret="${prefix}-vpn-csr"
bundle_secrets=("${environment}-deployment" "${environment}-osmo-images")
host_secrets=(
  "$csr_secret"
  "${prefix}-vpn-response"
  "${prefix}-osmo-token"
  "${prefix}-osmo-token-metadata"
  "${prefix}-registry-config"
  "${prefix}-osmo-artifacts"
)
vpn_secrets=(
  "${prefix}-vpn-config"
  "${prefix}-vpn-settings"
  "${prefix}-vpn-server-root"
  "${prefix}-vpn-client-root"
)
[[ "$with_vpn" == "true" ]] && host_secrets+=("${vpn_secrets[@]}")

# Inbound secrets get Secrets User; the host writes only its CSR.
reader_secrets=(
  "${bundle_secrets[@]}"
  "${prefix}-osmo-token"
  "${prefix}-osmo-token-metadata"
  "${prefix}-registry-config"
  "${prefix}-osmo-artifacts"
)
[[ "$with_vpn" == "true" ]] && reader_secrets+=("${prefix}-vpn-response" "${vpn_secrets[@]}")

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Key Vault" "$vault_name"
  print_kv "Bundle Secrets" "${bundle_secrets[*]} (must exist)"
  print_kv "Host Secrets" "${host_secrets[*]} (created when missing)"
  print_kv "Catalog" "$catalog_secret (written by 04)"
  if [[ -n "$registry_config_file" ]]; then
    print_kv "Registry" "from the bundle's image manifest"
    print_kv "Pull Token" "$token_name (scope map $scope_map)"
    print_kv "Password Expiry" "$token_expiry_utc"
    print_kv "Registry Config" "$registry_config_file"
    print_kv "Renew Password" "$renew_password"
  else
    print_kv "Pull Token" "skipped; pass --registry-config-file to create one"
  fi
  if [[ -n "$assignee_id" ]]; then
    print_kv "Assignee" "$assignee_id ($assignee_type)"
    print_kv "Secrets User" "${reader_secrets[*]} $catalog_secret (once published)"
    print_kv "Secrets Officer" "$([[ $with_vpn == true ]] && echo "$csr_secret" || echo 'none without --with-vpn')"
  else
    print_kv "Roles" "skipped; pass --assignee-object-id to grant per-secret roles"
  fi
  exit 0
fi

require_tools az jq
az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
work_dir=$(mktemp -d)
config_tmp=""
trap 'rm -rf "$work_dir" "$config_tmp"' EXIT

# Prints "present", "absent", or "error"; callers stop on "error".
secret_state() {
  local name="$1"
  if az keyvault secret show --vault-name "$vault_name" --name "$name" --query id -o tsv \
    >/dev/null 2>"$work_dir/secret-error.log"; then
    echo present
  elif hil_key_vault_secret_not_found "$work_dir/secret-error.log"; then
    echo absent
  else
    cat "$work_dir/secret-error.log" >&2
    echo error
  fi
}

#------------------------------------------------------------------------------
# Check the Bundle
#------------------------------------------------------------------------------
section "Check the Bundle"

for secret in "${bundle_secrets[@]}"; do
  state=$(secret_state "$secret")
  [[ "$state" != "error" ]] || fatal "Unable to read Key Vault secret $secret"
  [[ "$state" == "present" ]] || \
    fatal "$secret is missing; run upload-environment-bundle.sh --environment $environment with osmo-images.json from import-osmo-to-acr.sh"
done
image_manifest=$(az keyvault secret show --vault-name "$vault_name" --name "${environment}-osmo-images" \
  --query value -o tsv)
acr_name=$(jq -r '.registry // empty' <<< "$image_manifest")
login_server=$(jq -r '.login_server // empty' <<< "$image_manifest")
[[ -n "$acr_name" && -n "$login_server" ]] || fatal "${environment}-osmo-images has no registry or login server"
info "Bundle found; image manifest names $login_server"

#------------------------------------------------------------------------------
# Create Exchange Secrets
#------------------------------------------------------------------------------
section "Create Exchange Secrets"

secrets_created=0
secrets_existing=0
for secret in "${host_secrets[@]}"; do
  state=$(secret_state "$secret")
  [[ "$state" != "error" ]] || fatal "Unable to read Key Vault secret $secret"
  if [[ "$state" == "present" ]]; then
    secrets_existing=$((secrets_existing + 1))
    continue
  fi
  info "Creating $secret..."
  az keyvault secret set --vault-name "$vault_name" --name "$secret" --value placeholder \
    --content-type text/plain \
    --tags "physical-ai-environment=$environment" "physical-ai-host=$host_name" "physical-ai-exchange=placeholder" \
    --only-show-errors --output none
  secrets_created=$((secrets_created + 1))
done
catalog_state=$(secret_state "$catalog_secret")
[[ "$catalog_state" != "error" ]] || fatal "Unable to read Key Vault secret $catalog_secret"

#------------------------------------------------------------------------------
# Registry Pull Credential
#------------------------------------------------------------------------------
section "Registry Pull Credential"

write_registry_config() {
  local password="$1" auth config_dir
  config_dir=$(dirname "$registry_config_file")
  hil_prepare_directory "$config_dir"
  auth=$(printf '%s:%s' "$token_name" "$password" | base64 | tr -d '\n')
  config_tmp=$(mktemp "$config_dir/.registry-config.XXXXXX")
  chmod 0600 "$config_tmp"
  REGISTRY_AUTH="$auth" jq -n --arg host "$login_server" '{auths: {($host): {auth: env.REGISTRY_AUTH}}}' > "$config_tmp"
  mv "$config_tmp" "$registry_config_file"
  config_tmp=""
}

generate_password() {
  az acr token credential generate --name "$token_name" --registry "$acr_name" --password1 \
    --expiration "$token_expiry_utc" --query "passwords[?name=='password1'].value | [0]" -o tsv --only-show-errors
}

token_status="skipped"
if [[ -n "$registry_config_file" ]]; then
  live_login_server=$(az acr show --name "$acr_name" --query loginServer -o tsv)
  [[ "$live_login_server" == "$login_server" ]] || fatal "Registry $acr_name doesn't serve $login_server"
  token_count=$(az acr token list --registry "$acr_name" --query "length([?name=='$token_name'])" -o tsv)
  config_owner=""
  if [[ -e "$registry_config_file" ]]; then
    require_protected_file "$registry_config_file"
    jq -e --arg host "$login_server" '(.auths | keys) == [$host]' "$registry_config_file" >/dev/null || \
      fatal "Existing $registry_config_file isn't a single-entry config for $login_server; not overwriting it"
    config_owner=$(jq -r --arg host "$login_server" '.auths[$host].auth // empty' "$registry_config_file" | \
      base64 -d | cut -d: -f1) || fatal "Unable to read the existing registry config $registry_config_file"
  fi

  if [[ "$token_count" == "0" ]]; then
    [[ ! -e "$registry_config_file" ]] || \
      fatal "$registry_config_file exists but token $token_name doesn't; not overwriting it"
    info "Creating pull token $token_name with scope map $scope_map..."
    az acr token create --name "$token_name" --registry "$acr_name" --scope-map "$scope_map" \
      --no-passwords --only-show-errors --output none
    password=$(generate_password)
    [[ -n "$password" ]] || fatal "Token $token_name returned no password"
    write_registry_config "$password"
    unset password
    token_status="created"
  elif [[ -e "$registry_config_file" && "$config_owner" != "$token_name" ]]; then
    fatal "$registry_config_file belongs to another credential; not overwriting it"
  elif [[ "$renew_password" == "true" ]]; then
    warn "Regenerating the password for $token_name; the previous password stops working now"
    password=$(generate_password)
    [[ -n "$password" ]] || fatal "Token $token_name returned no password"
    write_registry_config "$password"
    unset password
    token_status="renewed"
  elif [[ -e "$registry_config_file" ]]; then
    password_expiry=$(az acr token show --name "$token_name" --registry "$acr_name" \
      --query "credentials.passwords[?name=='password1'].expiry | [0]" -o tsv)
    [[ -n "$password_expiry" ]] || \
      fatal "Token $token_name has no password1; pass --renew-registry-password to issue one"
    password_expiry="${password_expiry:0:19}Z"
    jq -n -e --arg expiry "$password_expiry" '$expiry | fromdateiso8601 > now' >/dev/null || \
      fatal "The password for $token_name expired at $password_expiry; pass --renew-registry-password"
    if jq -n -e --arg expiry "$password_expiry" --arg wanted "$token_expiry_utc" \
      '($expiry | fromdateiso8601) < ($wanted | fromdateiso8601)' >/dev/null; then
      warn "The password for $token_name expires at $password_expiry, before $token_expiry_utc; pass --renew-registry-password to extend it"
    fi
    info "Reusing token $token_name and $registry_config_file"
    token_status="reused"
  else
    fatal "Token $token_name exists but $registry_config_file doesn't; pass --renew-registry-password to issue a new password"
  fi
else
  info "No --registry-config-file; skipping the pull token"
fi

#------------------------------------------------------------------------------
# Grant Per-Secret Roles
#------------------------------------------------------------------------------
section "Grant Per-Secret Roles"

catalog_role="skipped"
if [[ -n "$assignee_id" ]]; then
  vault_id=$(az keyvault show --name "$vault_name" --query id -o tsv)
  broad_roles=$(az role assignment list --scope "$vault_id" --include-inherited \
    --query "[?principalId=='$assignee_id' && contains(['Key Vault Administrator', '$SECRETS_OFFICER_ROLE', '$SECRETS_USER_ROLE'], roleDefinitionName)].roleDefinitionName" \
    -o tsv)
  if [[ -n "$broad_roles" ]]; then
    warn "The assignee already holds $(tr '\n' ',' <<< "$broad_roles" | sed 's/,$//') on the vault or above; per-secret roles don't narrow that"
  fi
  for secret in "${reader_secrets[@]}"; do
    ensure_role_assignment "$assignee_id" "$assignee_type" "$SECRETS_USER_ROLE" "$vault_id/secrets/$secret"
  done
  if [[ "$with_vpn" == "true" ]]; then
    ensure_role_assignment "$assignee_id" "$assignee_type" "$SECRETS_OFFICER_ROLE" "$vault_id/secrets/$csr_secret"
  fi
  if [[ "$catalog_state" == "present" ]]; then
    ensure_role_assignment "$assignee_id" "$assignee_type" "$SECRETS_USER_ROLE" "$vault_id/secrets/$catalog_secret"
    catalog_role="granted"
  else
    catalog_role="pending; rerun after 04 publishes the catalog"
  fi
  info "New role assignments can take a few minutes to apply"
else
  info "No --assignee-object-id; skipping role assignments"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Environment" "$environment"
print_kv "Host" "$host_name"
print_kv "Key Vault" "$vault_name"
print_kv "Exchange Secrets" "$secrets_created created, $secrets_existing existing"
print_kv "Catalog" "$([[ $catalog_state == present ]] && echo 'published' || echo 'not yet published; 04 writes it')"
print_kv "Pull Token" "$token_name ($token_status)"
if [[ "$token_status" != "skipped" ]]; then
  print_kv "Registry Config" "$registry_config_file"
  print_kv "Password Expiry" "$token_expiry_utc"
fi
print_kv "Catalog Role" "$catalog_role"
if [[ "$token_status" == "renewed" ]]; then
  info "Rerun 04-prepare-osmo-hil-node.sh to publish the new config, then rerun 02-connect-osmo-backend.sh on the host"
else
  info "Next: run 04-prepare-osmo-hil-node.sh with --registry-config-file and the same token expiry"
fi
