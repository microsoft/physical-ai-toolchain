#!/usr/bin/env bash
# Establish one owned IKEv2 connection after private-only Key Vault access is verified.
# cspell:ignore cacerts closeaction dpdaction ikev keyexchange keyingtries leftcert leftfirewall leftid leftsourceip libstrongswan nameopt rightca rightid rightsubnet strongswan
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared helpers plus the VPN and K3s defaults.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"
# shellcheck source=../../defaults.conf
source "$SCRIPT_DIR/../../defaults.conf"

# Describe the explicit private-access checkpoint and the protected local inputs used for connection.
show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME --private-vault-verified [OPTIONS]

Connect one owned certificate-authenticated IKEv2 VPN after you have disabled
and verified Key Vault public access. This command uses only protected local VPN
inputs before connection and never opens the public window.

OPTIONS:
    -h, --help                    Show this help message
    -e, --environment NAME        Existing environment name (required)
    --host-name NAME              Host identity in the HiL catalog (required)
    --tenant-id ID                Expected Microsoft Entra tenant (required)
    --vault-name NAME             Expected Key Vault for post-VPN verification
    --subscription ID             Expected subscription for post-VPN verification
    --azure-config-dir DIR        Existing isolated Azure CLI state
    --input-dir DIR               Protected public VPN input directory
    --request-dir DIR             Protected local private-key directory
    --response-dir DIR            Protected signed public-response directory
    --connection-name NAME        Local strongSwan connection name
    --private-vault-verified      Confirm the documented private-only checkpoint
    --config-preview              Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \
      --vault-name <vault> --subscription <subscription> --private-vault-verified
EOF
}

environment=""
host_name=""
tenant_id=""
vault_name=""
subscription_id=""
azure_config_dir=""
input_dir=""
request_dir=""
response_dir=""
connection_name=""
private_vault_verified=false
config_preview=false

# Apply command-line values before deriving paths and validating the private-access checkpoint.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                  show_help; exit 0 ;;
    -e|--environment)           environment="$2"; shift 2 ;;
    --host-name)                host_name="$2"; shift 2 ;;
    --tenant-id)                tenant_id="$2"; shift 2 ;;
    --vault-name)               vault_name="$2"; shift 2 ;;
    --subscription)             subscription_id="$2"; shift 2 ;;
    --azure-config-dir)         azure_config_dir="$2"; shift 2 ;;
    --input-dir)                input_dir="$2"; shift 2 ;;
    --request-dir)              request_dir="$2"; shift 2 ;;
    --response-dir)             response_dir="$2"; shift 2 ;;
    --connection-name)          connection_name="$2"; shift 2 ;;
    --private-vault-verified)   private_vault_verified=true; shift ;;
    --config-preview)           config_preview=true; shift ;;
    *)                          fatal "Unknown option: $1" ;;
  esac
done

hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"
[[ "$private_vault_verified" == "true" ]] || fatal "--private-vault-verified is required"
connection_name="${connection_name:-physical-ai-${host_name}}"
[[ "$connection_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal "Invalid connection name: $connection_name"
input_dir="${input_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/public}"
request_dir="${request_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/request}"
response_dir="${response_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/vpn/${environment}-${host_name}/returned}"
azure_config_dir="${azure_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/azure/${environment}-${host_name}}"

# Show the planned connection and protected files, then exit before changing networking or DNS.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "reachable: VPN connected"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Tenant" "$tenant_id"
  print_kv "Connection" "$connection_name"
  print_kv "Private Vault Verified" "$private_vault_verified"
  print_kv "Public Inputs" "$input_dir"
  print_kv "Private Key" "$request_dir/client.key"
  print_kv "Public Response" "$response_dir/client.pem"
  print_kv "Pre-VPN Key Vault Access" "none"
  print_kv "Next" "02-connect-osmo-backend.sh"
  exit 0
fi

# Load the files and settings used to render the local connection.
require_tools apt-get az jq openssl sudo
catalog="$input_dir/catalog.json"
hil_validate_catalog "$catalog" "$environment" "$host_name" "$tenant_id" "$subscription_id" "$vault_name"
hil_validate_catalog_contract "$catalog" "$environment" "$host_name"
vpn_config=$(hil_require_local_artifact "$catalog" vpn_config "$environment" "$host_name" "$input_dir")
vpn_server_root=$(hil_require_local_artifact "$catalog" vpn_server_root "$environment" "$host_name" "$input_dir")
response_catalog="$response_dir/catalog.json"
hil_validate_catalog "$response_catalog" "$environment" "$host_name" "$tenant_id" "$subscription_id" "$vault_name"
hil_validate_catalog_contract "$response_catalog" "$environment" "$host_name"
vpn_response=$(hil_require_local_artifact "$response_catalog" vpn_response "$environment" "$host_name" "$response_dir")
private_key="$request_dir/client.key"
client_certificate="$response_dir/client.pem"
client_ca="$response_dir/client-ca.pem"
require_protected_file "$private_key"
require_protected_file "$client_certificate"
require_protected_file "$client_ca"
cmp -s <(jq -r '.client_certificate_pem' "$vpn_response") "$client_certificate" || \
  fatal "Local VPN client certificate does not match the cataloged response"
cmp -s <(jq -r '.client_ca_certificate_pem' "$vpn_response") "$client_ca" || \
  fatal "Local VPN client CA does not match the cataloged response"
gateway=$(jq -r '.gateway' "$vpn_config")
server_root_dn=$(openssl x509 -in "$vpn_server_root" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')
dns_server=$(jq -r '.private_dns.server // empty' "$vpn_config")
vpn_routes=$(jq -er '.private_routes | if type == "array" and length > 0 then .[] else error("missing routes") end' \
  "$vpn_config") || fatal "VPN configuration does not contain private routes"
mapfile -t private_routes <<< "$vpn_routes"
vpn_dns_zones=$(jq -ec '(.private_dns.zones // []) | if type == "array" then . else error("invalid DNS zones") end' \
  "$vpn_config") || fatal "VPN configuration contains invalid private DNS zones"
mapfile -t dns_zones < <(jq -r '.[]' <<< "$vpn_dns_zones")

# Install strongSwan and create or replace the local connection files.
sudo apt-get update
sudo apt-get install -y --no-install-recommends strongswan libstrongswan-extra-plugins
require_tools ipsec

state_dir=/var/lib/physical-ai-toolchain/vpn
owner_file="$state_dir/${connection_name}.json"
config_file="/etc/ipsec.d/${connection_name}.conf"
secret_file="/etc/ipsec.secrets.d/${connection_name}.secrets"
installed_client_ca="/etc/ipsec.d/cacerts/${connection_name}-client-ca.pem"
installed_server_ca="/etc/ipsec.d/cacerts/${connection_name}-server-ca.pem"
installed_client_certificate="/etc/ipsec.d/certs/${connection_name}.pem"
installed_private_key="/etc/ipsec.d/private/${connection_name}.key"
right_subnets=$(IFS=,; printf '%s' "${private_routes[*]}")
work_dir=$(mktemp -d)
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT
chmod 0700 "$work_dir"

# Build the requested strongSwan connection, secret, and ownership receipt.
cat > "$work_dir/connection.conf" <<EOF
conn $connection_name
    keyexchange=ikev2
    type=tunnel
    leftfirewall=yes
    left=%any
    leftcert=${connection_name}.pem
    leftauth=pubkey
    leftid=%$host_name
    leftsourceip=%config
    right=$gateway
    rightid=%$gateway
    rightca="$server_root_dn"
    rightsubnet=$right_subnets
    rightauth=pubkey
    auto=start
    dpdaction=restart
    closeaction=restart
    keyingtries=%forever
    esp=aes256gcm16
EOF
printf ': RSA %s.key\n' "$connection_name" > "$work_dir/connection.secrets"
jq -n --arg environment "$environment" --arg host "$host_name" --arg connection "$connection_name" \
  --arg gateway "$gateway" --arg config_sha "$(calculate_sha256 "$work_dir/connection.conf")" '
  {schema_version: 1, kind: "physical-ai-vpn-ownership", environment: $environment,
   host_name: $host, connection_name: $connection, gateway: $gateway, config_sha256: $config_sha}
' > "$work_dir/owner.json"
chmod 0600 "$work_dir/connection.conf" "$work_dir/connection.secrets" "$work_dir/owner.json"
sudo install -d -m 0700 "$state_dir"
sudo install -d -m 0755 /etc/ipsec.d /etc/ipsec.d/cacerts /etc/ipsec.d/certs /etc/ipsec.d/private /etc/ipsec.secrets.d
sudo install -m 0600 "$client_ca" "$installed_client_ca"
sudo install -m 0600 "$vpn_server_root" "$installed_server_ca"
sudo install -m 0600 "$client_certificate" "$installed_client_certificate"
sudo install -m 0600 "$private_key" "$installed_private_key"
sudo install -m 0600 "$work_dir/connection.conf" "$config_file"
sudo install -m 0600 "$work_dir/connection.secrets" "$secret_file"
sudo install -m 0600 "$work_dir/owner.json" "$owner_file"
sudo grep -Fqx 'include /etc/ipsec.d/*.conf' /etc/ipsec.conf || \
  printf '\ninclude /etc/ipsec.d/*.conf\n' | sudo tee -a /etc/ipsec.conf >/dev/null
sudo grep -Fqx 'include /etc/ipsec.secrets.d/*.secrets' /etc/ipsec.secrets || \
  printf '\ninclude /etc/ipsec.secrets.d/*.secrets\n' | sudo tee -a /etc/ipsec.secrets >/dev/null
sudo chmod 0600 /etc/ipsec.secrets

# Replace or remove the optional private DNS configuration.
dns_file="/etc/systemd/resolved.conf.d/90-physical-ai-${connection_name}.conf"
if [[ -n "$dns_server" ]]; then
  require_tools systemctl
  domains=""
  for zone in "${dns_zones[@]}"; do
    domains+=" ~$zone"
  done
  cat > "$work_dir/dns.conf" <<EOF
# Managed by physical-ai-toolchain optional HiL VPN
[Resolve]
DNS=$dns_server
Domains=${domains# }
EOF
  sudo install -d -m 0755 /etc/systemd/resolved.conf.d
  if ! sudo cmp --silent "$work_dir/dns.conf" "$dns_file" 2>/dev/null; then
    sudo install -m 0644 "$work_dir/dns.conf" "$dns_file"
    sudo systemctl restart systemd-resolved
  fi
elif sudo test -f "$dns_file"; then
  sudo rm -f "$dns_file"
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart systemd-resolved
  fi
fi

# Restart strongSwan and bring up the requested connection.
sudo ipsec restart
sudo ipsec up "$connection_name"
hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"
catalog_secret="${environment}-${host_name}-hil-catalog"
az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
  --name "$catalog_secret" --query id -o tsv >/dev/null

# Report the configured tunnel.
section "Deployment Summary"
print_kv "Milestone" "reachable: VPN connected"
print_kv "Connection" "$connection_name"
print_kv "Gateway" "$gateway"
print_kv "Private Routes" "${private_routes[*]}"
print_kv "Key Vault Verification" "passed"
print_kv "Next" "$REPO_ROOT/data-pipeline/setup/hil/02-connect-osmo-backend.sh"
info "Optional private reachability is ready"
