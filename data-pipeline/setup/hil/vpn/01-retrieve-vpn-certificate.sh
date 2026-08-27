#!/usr/bin/env bash
# Retrieve and install the signed public VPN response, then exit.
# cspell:ignore checkend noout outform pkey pubin pubout
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared helpers used for protected VPN artifact handling.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"

# Describe the public certificate retrieval and the private-only access checkpoint that follows it.
show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME [OPTIONS]

Retrieve and install the signed public VPN response. This command exits after
local installation so you can restore and verify private-only Key Vault access
before running the separate VPN connection command.

OPTIONS:
    -h, --help                  Show this help message
    -e, --environment NAME      Existing environment name (required)
    --host-name NAME            Host identity in the HiL catalog (required)
    --tenant-id ID              Expected Microsoft Entra tenant (required)
    --subscription ID           Expected Azure subscription (required)
    --vault-name NAME           Expected Key Vault (required)
    --request-dir DIR           Protected local private-key and CSR directory
    --response-dir DIR          Protected local public-response directory
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
request_dir=""
response_dir=""
azure_config_dir=""
config_preview=false

# Apply command-line values before deriving paths and validating the response target.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -e|--environment)    environment="$2"; shift 2 ;;
    --host-name)         host_name="$2"; shift 2 ;;
    --tenant-id)         tenant_id="$2"; shift 2 ;;
    --subscription)      subscription_id="$2"; shift 2 ;;
    --vault-name)        vault_name="$2"; shift 2 ;;
    --request-dir)       request_dir="$2"; shift 2 ;;
    --response-dir)      response_dir="$2"; shift 2 ;;
    --azure-config-dir)  azure_config_dir="$2"; shift 2 ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

# Require the values used by the retrieval path.
hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"

request_dir="${request_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/request}"
response_dir="${response_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/returned}"
azure_config_dir="${azure_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/azure/${environment}-${host_name}}"
catalog_secret="${environment}-${host_name}-hil-catalog"

# Show the expected certificate locations and security checkpoint, then exit without retrieving anything.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "reachable: VPN response"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Key Vault" "$vault_name"
  print_kv "Request Directory" "$request_dir"
  print_kv "Response Directory" "$response_dir"
  print_kv "VPN Mutation" "none"
  print_kv "Required Checkpoint" "restore and verify private-only Key Vault access after retrieval"
  print_kv "Next" "02-connect-vpn.sh --private-vault-verified"
  exit 0
fi

require_tools az jq
hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"

hil_fetch_artifacts "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$response_dir" vpn_response
catalog="$response_dir/catalog.json"
response_name=$(jq -r '.artifacts.vpn_response.file // empty' "$catalog")
response_file="$response_dir/$response_name"

# Replace the local certificate and CA files from the response.
client_tmp=$(mktemp "$response_dir/.client.XXXXXX")
ca_tmp=$(mktemp "$response_dir/.client-ca.XXXXXX")
jq -r '.client_certificate_pem' "$response_file" > "$client_tmp"
jq -r '.client_ca_certificate_pem' "$response_file" > "$ca_tmp"
chmod 0600 "$client_tmp" "$ca_tmp"
mv "$client_tmp" "$response_dir/client.pem"
mv "$ca_tmp" "$response_dir/client-ca.pem"

# Report the installed public response and remind the operator to restore private-only Key Vault access.
section "Deployment Summary"
print_kv "Milestone" "reachable: VPN response"
print_kv "Environment" "$environment"
print_kv "Host" "$host_name"
print_kv "Client Certificate" "$response_dir/client.pem"
print_kv "Client CA" "$response_dir/client-ca.pem"
print_kv "VPN Mutation" "none"
print_kv "Required Checkpoint" "disable and verify Key Vault public access now"
print_kv "Next" "$SCRIPT_DIR/02-connect-vpn.sh --private-vault-verified"
info "Signed VPN response is installed; this command is complete"
