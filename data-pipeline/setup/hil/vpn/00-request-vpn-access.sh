#!/usr/bin/env bash
# Retrieve exact public VPN inputs, generate a local private key, and publish only the CSR request.
# cspell:ignore addext noout outform pkey pubin pubout
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared helpers plus the VPN input defaults.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"
# shellcheck source=../../defaults.conf
source "$SCRIPT_DIR/../../defaults.conf"

# Describe the public-input retrieval, local key generation, and CSR handoff performed by this step.
show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME [OPTIONS]

Retrieve the exact public VPN inputs, generate the private key on Ubuntu, and
publish only a host-bound CSR request. Setup scripts do not change Key Vault
networking or RBAC. Open and close any required public window manually.

OPTIONS:
    -h, --help                  Show this help message
    -e, --environment NAME      Existing environment name (required)
    --host-name NAME            Host identity in the HiL catalog (required)
    --tenant-id ID              Expected Microsoft Entra tenant (required)
    --subscription ID           Expected Azure subscription (required)
    --vault-name NAME           Expected Key Vault (required)
    --input-dir DIR             Protected local public-input directory
    --request-dir DIR           Protected local private-key and CSR directory
    --azure-config-dir DIR      Isolated Azure CLI state directory
    --config-preview            Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \
      --tenant-id <tenant> --subscription <subscription> --vault-name <vault>
EOF
}

environment=""
host_name=""
tenant_id=""
subscription_id=""
vault_name=""
input_dir=""
request_dir=""
azure_config_dir=""
config_preview=false

# Apply command-line values before deriving paths and validating the VPN request target.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -e|--environment)    environment="$2"; shift 2 ;;
    --host-name)         host_name="$2"; shift 2 ;;
    --tenant-id)         tenant_id="$2"; shift 2 ;;
    --subscription)      subscription_id="$2"; shift 2 ;;
    --vault-name)        vault_name="$2"; shift 2 ;;
    --input-dir)         input_dir="$2"; shift 2 ;;
    --request-dir)       request_dir="$2"; shift 2 ;;
    --azure-config-dir)  azure_config_dir="$2"; shift 2 ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

# Require the values used by the request path.
hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"

input_dir="${input_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/public}"
request_dir="${request_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/request}"
azure_config_dir="${azure_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/azure/${environment}-${host_name}}"
catalog_secret="${environment}-${host_name}-hil-catalog"
csr_secret="${environment}-${host_name}-vpn-csr"

# Show the planned transfer and key/CSR locations, then exit before reading or writing any secret material.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "reachable: VPN request"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Tenant" "$tenant_id"
  print_kv "Subscription" "$subscription_id"
  print_kv "Key Vault" "$vault_name"
  print_kv "Catalog" "$catalog_secret"
  print_kv "Public Inputs" "$input_dir"
  print_kv "Private Key and CSR" "$request_dir"
  print_kv "CSR Destination" "$csr_secret"
  print_kv "Private Key Transfer" "never"
  print_kv "Next" "close and verify the public window; the CA owner signs the CSR"
  exit 0
fi

require_tools az jq openssl
hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"

hil_fetch_artifacts "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$input_dir" \
  vpn_config vpn_server_root vpn_client_root
catalog="$input_dir/catalog.json"
vpn_config=$(hil_require_local_artifact "$catalog" vpn_config "$environment" "$host_name" "$input_dir")
vpn_server_root=$(hil_require_local_artifact "$catalog" vpn_server_root "$environment" "$host_name" "$input_dir")
vpn_client_root=$(hil_require_local_artifact "$catalog" vpn_client_root "$environment" "$host_name" "$input_dir")

gateway=$(jq -r '.gateway' "$vpn_config")

# Reuse the private key and replace the CSR and request document on each run.
hil_prepare_directory "$request_dir"
private_key="$request_dir/client.key"
csr_file="$request_dir/client.csr"
request_file="$request_dir/vpn-request.json"
if [[ ! -f "$private_key" ]]; then
  private_key_tmp=$(mktemp "$request_dir/.client-key.XXXXXX")
  openssl genrsa -out "$private_key_tmp" 3072
  chmod 0600 "$private_key_tmp"
  mv "$private_key_tmp" "$private_key"
fi
csr_tmp=$(mktemp "$request_dir/.client-csr.XXXXXX")
openssl req -new -key "$private_key" -out "$csr_tmp" -subj "/CN=$host_name" \
  -addext "extendedKeyUsage=clientAuth"
chmod 0600 "$csr_tmp"
mv "$csr_tmp" "$csr_file"
client_root_sha=$(openssl x509 -in "$vpn_client_root" -outform der | calculate_sha256 /dev/stdin)
server_root_sha=$(openssl x509 -in "$vpn_server_root" -outform der | calculate_sha256 /dev/stdin)
request_tmp=$(mktemp "$request_dir/.vpn-request.XXXXXX")
jq -n --arg environment "$environment" --arg host "$host_name" --arg gateway "$gateway" \
  --arg csr_sha "$(calculate_sha256 "$csr_file")" --arg client_root_sha "$client_root_sha" \
  --arg server_root_sha "$server_root_sha" --rawfile csr "$csr_file" '
  {schema_version: 1, kind: "physical-ai-vpn-request", environment: $environment,
   host_name: $host, gateway: $gateway, csr_sha256: $csr_sha,
   client_root_sha256: $client_root_sha, server_root_sha256: $server_root_sha, csr_pem: $csr}
' > "$request_tmp"
chmod 0600 "$request_tmp"
mv "$request_tmp" "$request_file"

secret_error=$(mktemp "$request_dir/.key-vault-error.XXXXXX")
trap 'rm -f "$secret_error"' EXIT
if current_request=$(az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
  --name "$csr_secret" --query value -o tsv --only-show-errors 2>"$secret_error"); then
  :
elif hil_key_vault_secret_not_found "$secret_error"; then
  current_request=""
else
  fatal "Unable to read the existing VPN CSR from Key Vault"
fi
if [[ "$current_request" != "$(<"$request_file")" ]]; then
  az keyvault secret set --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$csr_secret" --file "$request_file" --encoding utf-8 --content-type application/json \
    --only-show-errors --output none
fi
rm -f "$secret_error"
trap - EXIT

# Report the CSR handoff and the checkpoint required before the CA response is retrieved.
section "Deployment Summary"
print_kv "Milestone" "reachable: VPN request"
print_kv "Environment" "$environment"
print_kv "Host" "$host_name"
print_kv "CSR" "$csr_file"
print_kv "CSR Handoff" "$csr_secret"
print_kv "Private Key" "remains only at $private_key"
print_kv "Required Checkpoint" "disable and verify Key Vault public access before continuing"
print_kv "Next" "The CA owner signs the CSR and publishes only the public response"
info "VPN access request is ready"
