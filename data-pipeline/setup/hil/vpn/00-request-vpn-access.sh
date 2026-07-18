#!/usr/bin/env bash
# Retrieve exact public VPN inputs, generate a local private key, and publish only the CSR request.
# cspell:ignore addext noout outform pkey pubin pubout
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"
# shellcheck source=../../defaults.conf
source "$SCRIPT_DIR/../../defaults.conf"

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
    --transport TRANSPORT       keyvault|scp (default: keyvault)
    --scp-source-dir DIR        Protected public-input directory for SCP transport
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
transport="keyvault"
scp_source_dir=""
input_dir=""
request_dir=""
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
    --input-dir)         input_dir="$2"; shift 2 ;;
    --request-dir)       request_dir="$2"; shift 2 ;;
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

input_dir="${input_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/public}"
request_dir="${request_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/request}"
azure_config_dir="${azure_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/azure/${environment}-${host_name}}"
catalog_secret="${environment}-${host_name}-hil-catalog"
csr_secret="${environment}-${host_name}-vpn-csr"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "reachable: VPN request"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Tenant" "$tenant_id"
  print_kv "Subscription" "$subscription_id"
  print_kv "Key Vault" "$vault_name"
  print_kv "Transfer" "$transport"
  print_kv "Catalog" "$catalog_secret"
  print_kv "Public Inputs" "$input_dir"
  print_kv "Private Key and CSR" "$request_dir"
  print_kv "CSR Destination" "$([[ $transport == keyvault ]] && echo "$csr_secret" || echo 'manual SCP handoff')"
  print_kv "Private Key Transfer" "never"
  print_kv "Next" "close and verify the public window; the CA owner signs the CSR"
  exit 0
fi

operation="validate VPN request inputs"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: reachable VPN request"
  [[ "$transport" != "keyvault" ]] || error "If a temporary Key Vault public window is open, disable and verify it now."
  exit "$status"
}
trap report_failure ERR

require_tools ip jq openssl python3
[[ "$transport" != "keyvault" ]] || require_tools az
if [[ "$transport" == "keyvault" ]]; then
  operation="authenticate to the expected Azure account"
  hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"
else
  require_protected_directory "$scp_source_dir"
fi

operation="retrieve exact public VPN inputs"
hil_fetch_artifacts "$transport" "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$input_dir" "$scp_source_dir" \
  vpn_config vpn_settings vpn_server_root vpn_client_root
catalog="$input_dir/catalog.json"
artifact_path() {
  local key="${1:?artifact key required}" file
  file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
  [[ "$file" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal "Catalog has an invalid file for $key"
  printf '%s/%s\n' "$input_dir" "$file"
}
vpn_config=$(artifact_path vpn_config)
vpn_settings=$(artifact_path vpn_settings)
vpn_server_root=$(artifact_path vpn_server_root)
vpn_client_root=$(artifact_path vpn_client_root)

operation="validate target-bound public VPN inputs"
jq -e --arg environment "$environment" --arg host "$host_name" '
  .schema_version == 1 and .kind == "physical-ai-vpn-inputs" and
  .environment == $environment and .host_name == $host and
  (.gateway | test("^[A-Za-z0-9.-]+$")) and
  (.p2s_cidr | type == "string" and length > 0) and
  (.private_routes | type == "array" and length > 0) and
  all(.private_routes[]; type == "string" and length > 0) and
  ((.private_dns // {}) | type == "object") and
  ((.private_dns.server // "") == "" or
    ((.private_dns | keys | sort) == (["probes", "server", "zones"] | sort) and
     (.private_dns.zones | type == "array" and length > 0) and
     all(.private_dns.zones[]; test("^[A-Za-z0-9.-]+$")) and
     (.private_dns.probes | type == "array" and length > 0) and
     all(.private_dns.probes[];
       (keys | sort) == (["expected_cidr", "host"] | sort) and
       (.host | test("^[A-Za-z0-9.-]+$")) and (.expected_cidr | type == "string"))))
' "$vpn_config" >/dev/null || fatal "VPN input metadata does not match the expected environment and host"
gateway=$(jq -r '.gateway' "$vpn_config")
grep -Fq "<VpnServer>$gateway</VpnServer>" "$vpn_settings" || \
  fatal "VPN settings do not contain the expected gateway"
openssl x509 -in "$vpn_server_root" -noout -subject -issuer >/dev/null
openssl x509 -in "$vpn_client_root" -noout -subject -issuer >/dev/null
mapfile -t networks < <(jq -r '.p2s_cidr, .private_routes[]' "$vpn_config")
default_interface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
[[ -n "$default_interface" ]] || fatal "No default-route interface is available for VPN overlap validation"
mapfile -t lan_networks < <(ip -o -4 addr show scope global | awk '{print $4}')
(( ${#lan_networks[@]} > 0 )) || fatal "No LAN IPv4 network is available for VPN overlap validation"
networks+=("$EDGE_K3S_POD_CIDR" "$EDGE_K3S_SERVICE_CIDR" "${lan_networks[@]}")
python3 "$SCRIPT_DIR/../check-network.py" "${networks[@]}"
dns_server=$(jq -r '.private_dns.server // empty' "$vpn_config")
if [[ -n "$dns_server" ]]; then
  dns_in_route=false
  while IFS= read -r route; do
    if python3 "$SCRIPT_DIR/../check-network.py" --address-in "$dns_server" "$route" >/dev/null 2>&1; then
      dns_in_route=true
      break
    fi
  done < <(jq -r '.private_routes[]' "$vpn_config")
  [[ "$dns_in_route" == "true" ]] || fatal "Private DNS server is outside the approved VPN routes"
fi

operation="generate the Ubuntu-owned private key and CSR"
hil_prepare_directory "$request_dir"
private_key="$request_dir/client.key"
csr_file="$request_dir/client.csr"
request_file="$request_dir/vpn-request.json"
existing=0
for file in "$private_key" "$csr_file" "$request_file"; do
  [[ ! -L "$file" ]] || fatal "VPN request path must not be a symlink: $file"
  [[ -e "$file" ]] && existing=$((existing + 1))
done
if (( existing == 0 )); then
  private_key_tmp=$(mktemp "$request_dir/.client-key.XXXXXX")
  csr_tmp=$(mktemp "$request_dir/.client-csr.XXXXXX")
  openssl genrsa -out "$private_key_tmp" 3072
  chmod 0600 "$private_key_tmp"
  openssl req -new -key "$private_key_tmp" -out "$csr_tmp" -subj "/CN=$host_name" \
    -addext "extendedKeyUsage=clientAuth"
  chmod 0600 "$csr_tmp"
  mv "$private_key_tmp" "$private_key"
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
elif (( existing != 3 )); then
  fatal "VPN request state is incomplete; inspect it without deleting the private key"
fi

for file in "$private_key" "$csr_file" "$request_file"; do
  require_protected_file "$file"
done
jq -e --arg environment "$environment" --arg host "$host_name" --arg sha "$(calculate_sha256 "$csr_file")" '
  (keys | sort) == (["client_root_sha256", "csr_pem", "csr_sha256", "environment", "gateway",
    "host_name", "kind", "schema_version", "server_root_sha256"] | sort) and
  .schema_version == 1 and .kind == "physical-ai-vpn-request" and
  .environment == $environment and .host_name == $host and .csr_sha256 == $sha and
  (.client_root_sha256 | test("^[0-9a-f]{64}$")) and (.server_root_sha256 | test("^[0-9a-f]{64}$"))
' "$request_file" >/dev/null || fatal "Existing VPN request does not match the environment, host, and CSR"
request_key=$(openssl req -in "$csr_file" -pubkey -noout | openssl pkey -pubin -outform der | openssl sha256)
private_key_hash=$(openssl pkey -in "$private_key" -pubout -outform der | openssl sha256)
[[ "$request_key" == "$private_key_hash" ]] || fatal "VPN CSR does not match the Ubuntu private key"

if [[ "$transport" == "keyvault" ]]; then
  operation="publish only the host-bound CSR request"
  [[ "$(jq -r '.csr_secret_name' "$catalog")" == "$csr_secret" ]] || fatal "Catalog CSR destination does not match"
  az keyvault secret set --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$csr_secret" --file "$request_file" --encoding utf-8 --content-type application/json \
    --only-show-errors --output none
else
  [[ ! -L "$scp_source_dir/vpn-request.json" ]] || fatal "SCP request destination must not be a symlink"
  install -m 0600 "$request_file" "$scp_source_dir/vpn-request.json"
fi

trap - ERR
section "Deployment Summary"
print_kv "Milestone" "reachable: VPN request"
print_kv "Environment" "$environment"
print_kv "Host" "$host_name"
print_kv "CSR" "$csr_file"
print_kv "CSR Handoff" "$([[ $transport == keyvault ]] && echo "$csr_secret" || echo "$scp_source_dir/vpn-request.json")"
print_kv "Private Key" "remains only at $private_key"
print_kv "Required Checkpoint" "disable and verify Key Vault public access before continuing"
print_kv "Next" "The CA owner signs the CSR and publishes only the public response"
info "VPN access request is ready"
