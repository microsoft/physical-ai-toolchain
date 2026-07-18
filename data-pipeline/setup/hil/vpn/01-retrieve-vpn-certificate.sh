#!/usr/bin/env bash
# Retrieve, validate, and install the signed public VPN response, then exit.
# cspell:ignore checkend noout outform pkey pubin pubout
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME [OPTIONS]

Retrieve and validate the signed public VPN response. This command exits after
local installation so you can restore and verify private-only Key Vault access
before running the separate VPN connection command.

OPTIONS:
    -h, --help                  Show this help message
    -e, --environment NAME      Existing environment name (required)
    --host-name NAME            Host identity in the HiL catalog (required)
    --tenant-id ID              Expected Microsoft Entra tenant (required)
    --subscription ID           Expected Azure subscription (required)
    --vault-name NAME           Expected Key Vault (required)
    --transport TRANSPORT       keyvault|scp (default: keyvault)
    --scp-source-dir DIR        Protected response directory for SCP transport
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
transport="keyvault"
scp_source_dir=""
request_dir=""
response_dir=""
azure_config_dir=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -e|--environment)    environment="$2"; shift 2 ;;
    --host-name)         host_name="$2"; shift 2 ;;
    --tenant-id)         tenant_id="$2"; shift 2 ;;
    --subscription)      subscription_id="$2"; shift 2 ;;
    --vault-name)        vault_name="$2"; shift 2 ;;
    --transport)         transport="$2"; shift 2 ;;
    --scp-source-dir)    scp_source_dir="$2"; shift 2 ;;
    --request-dir)       request_dir="$2"; shift 2 ;;
    --response-dir)      response_dir="$2"; shift 2 ;;
    --azure-config-dir)  azure_config_dir="$2"; shift 2 ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"
[[ "$transport" == "keyvault" || "$transport" == "scp" ]] || fatal "--transport must be keyvault or scp"
[[ "$transport" != "scp" || -n "$scp_source_dir" ]] || fatal "--scp-source-dir is required for SCP transport"

request_dir="${request_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/request}"
response_dir="${response_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/returned}"
azure_config_dir="${azure_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/azure/${environment}-${host_name}}"
catalog_secret="${environment}-${host_name}-hil-catalog"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "reachable: VPN response"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Key Vault" "$vault_name"
  print_kv "Transfer" "$transport"
  print_kv "Request Directory" "$request_dir"
  print_kv "Response Directory" "$response_dir"
  print_kv "VPN Mutation" "none"
  print_kv "Required Checkpoint" "restore and verify private-only Key Vault access after retrieval"
  print_kv "Next" "02-connect-vpn.sh --private-vault-verified"
  exit 0
fi

operation="validate VPN response inputs"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: reachable VPN response"
  [[ "$transport" != "keyvault" ]] || error "If a temporary Key Vault public window is open, disable and verify it now."
  exit "$status"
}
trap report_failure ERR

require_tools jq openssl
[[ "$transport" != "keyvault" ]] || require_tools az
require_protected_directory "$request_dir"
require_external_runtime_path "$request_dir"
require_external_runtime_path "$response_dir"
private_key="$request_dir/client.key"
csr_file="$request_dir/client.csr"
request_file="$request_dir/vpn-request.json"
require_protected_file "$private_key"
require_protected_file "$csr_file"
require_protected_file "$request_file"
if [[ "$transport" == "keyvault" ]]; then
  operation="authenticate to the expected Azure account"
  hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"
else
  require_protected_directory "$scp_source_dir"
fi

operation="retrieve exact signed public response"
hil_fetch_artifacts "$transport" "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$response_dir" "$scp_source_dir" vpn_response
catalog="$response_dir/catalog.json"
response_name=$(jq -r '.artifacts.vpn_response.file // empty' "$catalog")
[[ "$response_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal "Catalog has an invalid VPN response file"
response_file="$response_dir/$response_name"
require_protected_file "$response_file"

operation="validate signed public response"
jq -e --arg environment "$environment" --arg host "$host_name" --arg csr_sha "$(calculate_sha256 "$csr_file")" '
  (keys | sort) == (["client_ca_certificate_pem", "client_certificate_pem", "client_root_sha256",
    "csr_sha256", "environment", "gateway", "host_name", "kind", "schema_version",
    "server_root_sha256"] | sort) and
  .schema_version == 1 and .kind == "physical-ai-vpn-response" and
  .environment == $environment and .host_name == $host and .csr_sha256 == $csr_sha and
  (.client_certificate_pem | contains("BEGIN CERTIFICATE")) and
  (.client_ca_certificate_pem | contains("BEGIN CERTIFICATE"))
' "$response_file" >/dev/null || fatal "VPN response does not bind to this host and CSR"
hil_reject_private_key_material "$response_file"
jq -e --arg gateway "$(jq -r '.gateway' "$request_file")" \
  --arg client_root "$(jq -r '.client_root_sha256' "$request_file")" \
  --arg server_root "$(jq -r '.server_root_sha256' "$request_file")" '
  .gateway == $gateway and .client_root_sha256 == $client_root and .server_root_sha256 == $server_root
' "$response_file" >/dev/null || fatal "VPN response does not match the requested gateway and trust roots"
for target in "$response_dir/client.pem" "$response_dir/client-ca.pem"; do
  [[ ! -L "$target" ]] || fatal "VPN response target must not be a symlink: $target"
done
client_tmp=$(mktemp "$response_dir/.client.XXXXXX")
ca_tmp=$(mktemp "$response_dir/.client-ca.XXXXXX")
jq -r '.client_certificate_pem' "$response_file" > "$client_tmp"
jq -r '.client_ca_certificate_pem' "$response_file" > "$ca_tmp"
chmod 0600 "$client_tmp" "$ca_tmp"
openssl verify -CAfile "$ca_tmp" "$client_tmp" >/dev/null
openssl x509 -in "$client_tmp" -noout -checkend 86400 >/dev/null || \
  fatal "Client certificate expires within 24 hours"
openssl x509 -in "$client_tmp" -noout -text | \
  grep -A2 'Extended Key Usage' | grep -q 'TLS Web Client Authentication' || \
  fatal "Client certificate does not contain the clientAuth extended key usage"
cert_key=$(openssl x509 -in "$client_tmp" -pubkey -noout | \
  openssl pkey -pubin -outform der | openssl sha256)
private_key_hash=$(openssl pkey -in "$private_key" -pubout -outform der | openssl sha256)
[[ "$cert_key" == "$private_key_hash" ]] || fatal "Client certificate does not match the Ubuntu private key"
csr_key=$(openssl req -in "$csr_file" -pubkey -noout | openssl pkey -pubin -outform der | openssl sha256)
[[ "$cert_key" == "$csr_key" ]] || fatal "Client certificate does not match the original CSR"
client_root_sha=$(openssl x509 -in "$ca_tmp" -outform der | calculate_sha256 /dev/stdin)
[[ "$client_root_sha" == "$(jq -r '.client_root_sha256' "$response_file")" ]] || \
  fatal "Returned client chain does not match the requested client root"
mv "$client_tmp" "$response_dir/client.pem"
mv "$ca_tmp" "$response_dir/client-ca.pem"

trap - ERR
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
