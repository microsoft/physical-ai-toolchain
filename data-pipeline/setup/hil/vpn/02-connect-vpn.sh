#!/usr/bin/env bash
# Establish one owned IKEv2 connection after private-only Key Vault access is verified.
# cspell:ignore ahosts ahostsv cacerts closeaction dpdaction ikev keyexchange keyingtries leftcert leftfirewall leftid leftsourceip libstrongswan nameopt noout outform pkey pubin pubout resolvectl rightca rightid rightsubnet strongswan xfrm
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
    --transport TRANSPORT         keyvault|scp (default: keyvault)
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
      --transport keyvault --vault-name <vault> --subscription <subscription> \
      --private-vault-verified
EOF
}

environment=""
host_name=""
tenant_id=""
transport="keyvault"
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
    --transport)                transport="$2"; shift 2 ;;
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
[[ "$transport" == "keyvault" || "$transport" == "scp" ]] || fatal "--transport must be keyvault or scp"
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
  print_kv "Transport" "$transport"
  print_kv "Private Vault Verified" "$private_vault_verified"
  print_kv "Public Inputs" "$input_dir"
  print_kv "Private Key" "$request_dir/client.key"
  print_kv "Public Response" "$response_dir/client.pem"
  print_kv "Pre-VPN Key Vault Access" "none"
  print_kv "Next" "02-connect-osmo-backend.sh"
  exit 0
fi

# Require explicit confirmation that private-only Key Vault access was restored before connecting.
[[ "$private_vault_verified" == "true" ]] || \
  fatal "Complete the documented private-only Key Vault verification, then pass --private-vault-verified"

# Track network mutations and define a verified rollback path for VPN, DNS, routes, and owned files.
operation="validate protected local VPN inputs"
vpn_mutation_started=false
rollback_vpn_mutation() {
  local rollback_failed=false
  set +o errexit
  sudo ipsec down "${connection_name:-}" >/dev/null 2>&1 || true
  if [[ -n "${work_dir:-}" && -f "$work_dir/ipsec.conf.before" ]]; then
    sudo install -m 0644 "$work_dir/ipsec.conf.before" /etc/ipsec.conf || rollback_failed=true
    sudo install -m 0600 "$work_dir/ipsec.secrets.before" /etc/ipsec.secrets || rollback_failed=true
    for item in owner config secret client-ca server-ca client-cert private-key; do
      case "$item" in
        owner) target="$owner_file" ;;
        config) target="$config_file" ;;
        secret) target="$secret_file" ;;
        client-ca) target="$installed_client_ca" ;;
        server-ca) target="$installed_server_ca" ;;
        client-cert) target="$installed_client_certificate" ;;
        private-key) target="$installed_private_key" ;;
      esac
      if [[ -f "$work_dir/$item.before" ]]; then
        sudo install -m 0600 "$work_dir/$item.before" "$target" || rollback_failed=true
      else
        sudo rm -f "$target" || rollback_failed=true
      fi
    done
    if [[ -n "${dns_file:-}" ]]; then
      if [[ -f "$work_dir/dns.before" ]]; then
        sudo install -m 0644 "$work_dir/dns.before" "$dns_file" || rollback_failed=true
      else
        sudo rm -f "$dns_file" || rollback_failed=true
      fi
      sudo systemctl restart systemd-resolved || rollback_failed=true
    fi
    sudo ipsec restart >/dev/null 2>&1 || rollback_failed=true
    if [[ "${prior_connection_up:-false}" == "true" ]]; then
      sudo ipsec up "$connection_name" >/dev/null 2>&1 || rollback_failed=true
    fi
    [[ "$(ip -4 route show default)" == "$default_route_before" ]] || rollback_failed=true
    getent ahosts "${public_canary:-mcr.microsoft.com}" >/dev/null || rollback_failed=true
  fi
  set -o errexit
  [[ "$rollback_failed" == "false" ]]
}

# Restore prior state when a failure occurs after the VPN or DNS mutation begins.
report_failure() {
  local status=$?
  trap - ERR
  if [[ "$vpn_mutation_started" == "true" ]]; then
    if rollback_vpn_mutation; then
      error "Prior VPN and DNS state restored after failure."
    else
      error "Automatic VPN restoration did not verify; inspect the owned state before retrying."
    fi
    vpn_mutation_started=false
  fi
  error "Operation failed: $operation"
  error "Milestone incomplete: reachable VPN connected"
  exit "$status"
}
trap report_failure ERR

# Validate protected local catalogs, certificates, private key material, and the expected target-bound response.
require_tools apt-get getent ip jq openssl python3 sudo
require_external_runtime_path "$input_dir"
require_external_runtime_path "$request_dir"
require_external_runtime_path "$response_dir"
[[ "$transport" != "keyvault" ]] || require_external_runtime_path "$azure_config_dir"
require_protected_directory "$input_dir"
require_protected_directory "$request_dir"
require_protected_directory "$response_dir"
catalog="$input_dir/catalog.json"
response_catalog="$response_dir/catalog.json"
hil_validate_catalog "$catalog" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name"
hil_validate_catalog "$response_catalog" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name"
vpn_config=$(hil_require_local_artifact "$catalog" vpn_config "$environment" "$host_name" "$input_dir")
vpn_server_root=$(hil_require_local_artifact "$catalog" vpn_server_root "$environment" "$host_name" "$input_dir")
hil_require_local_artifact "$catalog" vpn_settings "$environment" "$host_name" "$input_dir" >/dev/null
vpn_client_root=$(hil_require_local_artifact "$catalog" vpn_client_root "$environment" "$host_name" "$input_dir")
response_file=$(hil_require_local_artifact "$response_catalog" vpn_response "$environment" "$host_name" "$response_dir")
private_key="$request_dir/client.key"
csr_file="$request_dir/client.csr"
client_certificate="$response_dir/client.pem"
client_ca="$response_dir/client-ca.pem"
for file in "$catalog" "$vpn_config" "$vpn_server_root" "$response_catalog" "$response_file" \
  "$vpn_client_root" "$private_key" "$csr_file" "$client_certificate" "$client_ca"; do
  require_protected_file "$file"
done
jq -e --arg environment "$environment" --arg host "$host_name" '
  ((keys - ["schema_version", "kind", "environment", "host_name", "gateway", "p2s_cidr",
    "private_routes", "private_dns", "public_dns_canary"]) | length) == 0 and
  .schema_version == 1 and .kind == "physical-ai-vpn-inputs" and
  .environment == $environment and .host_name == $host and
  (.gateway | test("^[A-Za-z0-9.-]+$")) and (.p2s_cidr | type == "string" and length > 0) and
  (.private_routes | type == "array" and length > 0) and all(.private_routes[]; type == "string") and
  ((.private_dns.server // "") == "" or
    ((.private_dns | keys | sort) == (["probes", "server", "zones"] | sort) and
     (.private_dns.zones | type == "array" and length > 0) and
     all(.private_dns.zones[]; test("^[A-Za-z0-9.-]+$")) and
     (.private_dns.probes | type == "array" and length > 0) and
     all(.private_dns.probes[];
       (keys | sort) == (["expected_cidr", "host"] | sort) and
       (.host | test("^[A-Za-z0-9.-]+$")) and (.expected_cidr | type == "string"))))
' "$vpn_config" >/dev/null || fatal "VPN input metadata does not match the environment and host"
gateway=$(jq -r '.gateway' "$vpn_config")
p2s_cidr=$(jq -r '.p2s_cidr' "$vpn_config")
mapfile -t private_routes < <(jq -r '.private_routes[]' "$vpn_config")
default_interface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
[[ -n "$default_interface" ]] || fatal "No default-route interface is available for VPN overlap validation"
mapfile -t lan_networks < <(ip -o -4 addr show scope global | awk '{print $4}')
(( ${#lan_networks[@]} > 0 )) || fatal "No LAN IPv4 network is available for VPN overlap validation"
python3 "$SCRIPT_DIR/../check-network.py" "$p2s_cidr" "${private_routes[@]}" \
  "$EDGE_K3S_POD_CIDR" "$EDGE_K3S_SERVICE_CIDR" "${lan_networks[@]}"
openssl verify -CAfile "$vpn_client_root" "$client_certificate" >/dev/null
openssl x509 -in "$client_ca" -noout -text | grep -A2 'Basic Constraints' | grep -q 'CA:TRUE' || \
  fatal "Client root certificate is not a CA"
openssl x509 -in "$vpn_server_root" -noout -text | grep -A2 'Basic Constraints' | grep -q 'CA:TRUE' || \
  fatal "VPN server root certificate is not a CA"
openssl x509 -in "$client_certificate" -noout -text | grep -A2 'Basic Constraints' | grep -q 'CA:FALSE' || \
  fatal "Client certificate must have CA:FALSE"
openssl x509 -in "$client_certificate" -noout -subject -nameopt RFC2253 | grep -Fq "CN=$host_name" || \
  fatal "Client certificate subject does not match the host identity"
cert_key=$(openssl x509 -in "$client_certificate" -pubkey -noout | openssl pkey -pubin -outform der | openssl sha256)
private_key_hash=$(openssl pkey -in "$private_key" -pubout -outform der | openssl sha256)
[[ "$cert_key" == "$private_key_hash" ]] || fatal "Client certificate does not match the Ubuntu private key"
client_root_sha=$(openssl x509 -in "$client_ca" -outform der | calculate_sha256 /dev/stdin)
original_client_root_sha=$(openssl x509 -in "$vpn_client_root" -outform der | calculate_sha256 /dev/stdin)
server_root_sha=$(openssl x509 -in "$vpn_server_root" -outform der | calculate_sha256 /dev/stdin)
server_root_dn=$(openssl x509 -in "$vpn_server_root" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')
jq -e '
  (keys | sort) == (["client_ca_certificate_pem", "client_certificate_pem", "client_root_sha256",
    "csr_sha256", "environment", "gateway", "host_name", "kind", "schema_version",
    "server_root_sha256"] | sort)
' "$response_file" >/dev/null || fatal "Installed VPN response has unexpected fields"
hil_reject_private_key_material "$response_file"
jq -e --arg environment "$environment" --arg host "$host_name" --arg gateway "$gateway" \
  --arg client_root "$client_root_sha" --arg server_root "$server_root_sha" \
  --arg original_client_root "$original_client_root_sha" --arg csr_sha "$(calculate_sha256 "$csr_file")" '
  .schema_version == 1 and .kind == "physical-ai-vpn-response" and
  .environment == $environment and .host_name == $host and .gateway == $gateway and
  .client_root_sha256 == $client_root and .client_root_sha256 == $original_client_root and
  .server_root_sha256 == $server_root and .csr_sha256 == $csr_sha
' "$response_file" >/dev/null || fatal "Installed VPN response does not match the selected CSR, gateway, and trust roots"

# Validate DNS settings, route overlap, and public name resolution before changing the host network.
dns_server=$(jq -r '.private_dns.server // empty' "$vpn_config")
mapfile -t dns_zones < <(jq -r '.private_dns.zones[]? // empty' "$vpn_config")
mapfile -t private_probes < <(jq -r '.private_dns.probes[]? | [.host, .expected_cidr] | @tsv' "$vpn_config")
public_canary=$(jq -r '.public_dns_canary // "mcr.microsoft.com"' "$vpn_config")
getent ahosts "$public_canary" >/dev/null || fatal "Public DNS canary is unresolved before VPN mutation"
if [[ -n "$dns_server" ]]; then
  dns_in_route=false
  for route in "${private_routes[@]}"; do
    if python3 "$SCRIPT_DIR/../check-network.py" --address-in "$dns_server" "$route" >/dev/null 2>&1; then
      dns_in_route=true
      break
    fi
  done
  [[ "$dns_in_route" == "true" ]] || fatal "Private DNS server is outside the approved VPN routes"
fi

# Install the strongSwan packages needed to create and operate the certificate-authenticated tunnel.
operation="install optional VPN packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends strongswan libstrongswan-extra-plugins
require_tools ipsec

# Prepare owned system paths, temporary state, and cleanup behavior for an idempotent VPN installation.
operation="install owned strongSwan connection"
state_dir=/var/lib/physical-ai-toolchain/vpn
owner_file="$state_dir/${connection_name}.json"
config_file="/etc/ipsec.d/${connection_name}.conf"
secret_file="/etc/ipsec.secrets.d/${connection_name}.secrets"
installed_client_ca="/etc/ipsec.d/cacerts/${connection_name}-client-ca.pem"
installed_server_ca="/etc/ipsec.d/cacerts/${connection_name}-server-ca.pem"
installed_client_certificate="/etc/ipsec.d/certs/${connection_name}.pem"
installed_private_key="/etc/ipsec.d/private/${connection_name}.key"
for directory in "$state_dir" /etc/ipsec.d /etc/ipsec.d/cacerts /etc/ipsec.d/certs \
  /etc/ipsec.d/private /etc/ipsec.secrets.d /etc/systemd/resolved.conf.d; do
  require_no_symlink_path "$directory"
done
for path in "$owner_file" "$config_file" "$secret_file" "$installed_client_ca" \
  "$installed_server_ca" "$installed_client_certificate" "$installed_private_key" \
  /etc/ipsec.conf /etc/ipsec.secrets; do
  sudo test ! -L "$path" || fatal "Owned VPN path must not be a symlink: $path"
done
right_subnets=$(IFS=,; printf '%s' "${private_routes[*]}")
work_dir=$(mktemp -d)
cleanup() {
  local status=$?
  trap - ERR
  if [[ "$vpn_mutation_started" == "true" && "$status" -ne 0 ]]; then
    if rollback_vpn_mutation; then
      error "Prior VPN and DNS state restored after failure."
    else
      error "Automatic VPN restoration did not verify; inspect the owned state before retrying."
    fi
    error "Operation failed: $operation"
    error "Milestone incomplete: reachable VPN connected"
    vpn_mutation_started=false
  fi
  rm -rf "$work_dir"
  exit "$status"
}
trap cleanup EXIT
chmod 0700 "$work_dir"
had_owner=false
had_config=false
had_secret=false
had_dns=false
sudo test -f "$owner_file" && had_owner=true
sudo test -f "$config_file" && had_config=true
sudo test -f "$secret_file" && had_secret=true
prior_connection_up=false
if [[ "$had_owner" == "true" ]] && command -v ipsec >/dev/null 2>&1 && \
   ipsec status "$connection_name" 2>/dev/null | grep -q ESTABLISHED; then
  prior_connection_up=true
fi
if [[ "$had_owner" == "true" ]]; then
  [[ "$had_config" == "true" && "$had_secret" == "true" ]] || \
    fatal "Owned VPN state is incomplete; inspect it before continuing"
  for path in "$installed_client_ca" "$installed_server_ca" "$installed_client_certificate" "$installed_private_key"; do
    sudo test -f "$path" || fatal "Owned VPN state is incomplete: $path"
  done
elif [[ "$had_config" == "true" || "$had_secret" == "true" ]] || \
     sudo test -e "$installed_client_ca" || sudo test -e "$installed_server_ca" || \
     sudo test -e "$installed_client_certificate" || sudo test -e "$installed_private_key"; then
  fatal "Existing VPN files are not owned by this setup path"
fi

# Build the requested strongSwan connection, secret, and ownership receipt from validated inputs.
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
if sudo test -e "$owner_file"; then
  sudo cat "$owner_file" | jq -e --arg environment "$environment" --arg host "$host_name" \
    --arg connection "$connection_name" --arg gateway "$gateway" --arg sha "$(calculate_sha256 "$work_dir/connection.conf")" '
    .schema_version == 1 and .kind == "physical-ai-vpn-ownership" and
    .environment == $environment and .host_name == $host and .connection_name == $connection and
    .gateway == $gateway and .config_sha256 == $sha
  ' >/dev/null || fatal "Existing VPN ownership state identifies a different target"
  sudo cmp --silent "$work_dir/connection.conf" "$config_file" || fatal "Owned VPN configuration has drifted"
fi

# Snapshot existing VPN and DNS state, then install only files owned by this setup path.
default_route_before=$(ip -4 route show default)
sudo cp -p /etc/ipsec.conf "$work_dir/ipsec.conf.before"
sudo cp -p /etc/ipsec.secrets "$work_dir/ipsec.secrets.before"
[[ "$had_owner" == "false" ]] || sudo cp -p "$owner_file" "$work_dir/owner.before"
[[ "$had_config" == "false" ]] || sudo cp -p "$config_file" "$work_dir/config.before"
[[ "$had_secret" == "false" ]] || sudo cp -p "$secret_file" "$work_dir/secret.before"
[[ "$had_owner" == "false" ]] || sudo cp -p "$installed_client_ca" "$work_dir/client-ca.before"
[[ "$had_owner" == "false" ]] || sudo cp -p "$installed_server_ca" "$work_dir/server-ca.before"
[[ "$had_owner" == "false" ]] || sudo cp -p "$installed_client_certificate" "$work_dir/client-cert.before"
[[ "$had_owner" == "false" ]] || sudo cp -p "$installed_private_key" "$work_dir/private-key.before"
vpn_mutation_started=true
sudo install -d -m 0700 "$state_dir"
sudo install -d -m 0755 /etc/ipsec.d /etc/ipsec.d/cacerts /etc/ipsec.d/certs /etc/ipsec.d/private /etc/ipsec.secrets.d
sudo install -m 0600 "$client_ca" "/etc/ipsec.d/cacerts/${connection_name}-client-ca.pem"
sudo install -m 0600 "$vpn_server_root" "/etc/ipsec.d/cacerts/${connection_name}-server-ca.pem"
sudo install -m 0600 "$client_certificate" "/etc/ipsec.d/certs/${connection_name}.pem"
sudo install -m 0600 "$private_key" "/etc/ipsec.d/private/${connection_name}.key"
sudo install -m 0600 "$work_dir/connection.conf" "$config_file"
sudo install -m 0600 "$work_dir/connection.secrets" "$secret_file"
sudo install -m 0600 "$work_dir/owner.json" "$owner_file"
sudo grep -Fqx 'include /etc/ipsec.d/*.conf' /etc/ipsec.conf || \
  printf '\ninclude /etc/ipsec.d/*.conf\n' | sudo tee -a /etc/ipsec.conf >/dev/null
sudo grep -Fqx 'include /etc/ipsec.secrets.d/*.secrets' /etc/ipsec.secrets || \
  printf '\ninclude /etc/ipsec.secrets.d/*.secrets\n' | sudo tee -a /etc/ipsec.secrets >/dev/null
sudo chmod 0600 /etc/ipsec.secrets

# Start the tunnel and verify its address, protected routes, and unchanged public default route.
operation="start and validate the owned VPN connection"
sudo ipsec restart
sudo ipsec up "$connection_name"
default_route_after=$(ip -4 route show default)
[[ "$default_route_after" == "$default_route_before" ]] || fatal "VPN setup changed the public default route"
ipsec status "$connection_name" | grep -q ESTABLISHED || fatal "IKEv2 connection is not established"
mapfile -t assigned_addresses < <(ip -o -4 addr show scope global | awk '{print $4}' | cut -d/ -f1)
p2s_address=""
for address in "${assigned_addresses[@]}"; do
  if python3 "$SCRIPT_DIR/../check-network.py" --address-in "$address" "$p2s_cidr" >/dev/null 2>&1; then
    p2s_address="$address"
    break
  fi
done
[[ -n "$p2s_address" ]] || fatal "No assigned address belongs to the expected VPN client pool"
for route in "${private_routes[@]}"; do
  ip xfrm policy | grep -F "dst $route" >/dev/null || fatal "No XFRM policy protects private route $route"
  route_host=$(python3 "$SCRIPT_DIR/../check-network.py" --first-host "$route")
  ip route get "$route_host" >/dev/null
done

# Configure route-only private DNS when the VPN contract supplies it and verify public and private lookups.
if [[ -n "$dns_server" ]]; then
  operation="install and validate route-only private DNS"
  require_tools resolvectl systemctl
  dns_file="/etc/systemd/resolved.conf.d/90-physical-ai-${connection_name}.conf"
  sudo test ! -L "$dns_file" || fatal "Owned DNS path must not be a symlink: $dns_file"
  sudo test -f "$dns_file" && had_dns=true
  [[ "$had_dns" == "false" || "$had_owner" == "true" ]] || \
    fatal "Existing private DNS state is not owned by this setup path"
  [[ "$had_dns" == "false" ]] || sudo cp -p "$dns_file" "$work_dir/dns.before"
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
  if sudo test -e "$dns_file"; then
    sudo cmp --silent "$work_dir/dns.conf" "$dns_file" || fatal "Owned private DNS configuration has drifted"
  else
    sudo install -m 0644 "$work_dir/dns.conf" "$dns_file"
  fi
  sudo systemctl restart systemd-resolved
  if ! resolvectl query "$public_canary" >/dev/null; then
    if [[ "$had_dns" == "false" ]]; then
      sudo rm -f "$dns_file"
    else
      sudo install -m 0644 "$work_dir/dns.before" "$dns_file"
    fi
    sudo systemctl restart systemd-resolved
    resolvectl query "$public_canary" >/dev/null || fatal "Public DNS failed and prior DNS restoration did not verify"
    fatal "Public DNS failed after private DNS setup; the prior DNS state was restored"
  fi
  for probe in "${private_probes[@]}"; do
    IFS=$'\t' read -r host expected_cidr <<< "$probe"
    if ! probe_output=$(resolvectl query --legend=no "$host"); then
      if [[ "$had_dns" == "false" ]]; then
        sudo rm -f "$dns_file"
      else
        sudo install -m 0644 "$work_dir/dns.before" "$dns_file"
      fi
      sudo systemctl restart systemd-resolved
      resolvectl query "$public_canary" >/dev/null || fatal "Private DNS failed and prior DNS restoration did not verify"
      fatal "Private DNS validation failed; the prior DNS state was restored"
    fi
    mapfile -t probe_addresses < <(grep -Eo '([0-9]{1,3}\.){3}[0-9]{1,3}' <<< "$probe_output" | LC_ALL=C sort -u)
    (( ${#probe_addresses[@]} > 0 )) || fatal "Private DNS probe returned no IPv4 address: $host"
    for probe_address in "${probe_addresses[@]}"; do
      python3 "$SCRIPT_DIR/../check-network.py" --address-in "$probe_address" "$expected_cidr" >/dev/null || \
        fatal "Private DNS answer for $host is outside $expected_cidr"
    done
  done
else
  dns_file="/etc/systemd/resolved.conf.d/90-physical-ai-${connection_name}.conf"
  sudo test ! -e "$dns_file" || fatal "Owned private DNS state exists but the selected VPN contract has no private DNS"
  operation="validate public DNS preservation"
  getent ahosts "$public_canary" >/dev/null
fi

# Confirm the expected Key Vault hostname resolves and the data-plane request uses an approved private route.
if [[ "$transport" == "keyvault" ]]; then
  operation="verify private Key Vault data-plane reachability"
  require_tools az
  require_protected_directory "$azure_config_dir"
  export AZURE_CONFIG_DIR="$azure_config_dir"
  vault_host="${vault_name}.vault.azure.net"
  mapfile -t vault_addresses < <(getent ahostsv4 "$vault_host" | awk '{print $1}' | LC_ALL=C sort -u)
  (( ${#vault_addresses[@]} > 0 )) || fatal "Private Key Vault hostname did not resolve"
  vault_private_route=false
  for address in "${vault_addresses[@]}"; do
    for route in "${private_routes[@]}"; do
      if python3 "$SCRIPT_DIR/../check-network.py" --address-in "$address" "$route" >/dev/null 2>&1; then
        ip route get "$address" >/dev/null
        vault_private_route=true
        break 2
      fi
    done
  done
  [[ "$vault_private_route" == "true" ]] || fatal "Key Vault hostname does not resolve inside the approved private routes"
  az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "${environment}-${host_name}-hil-catalog" --query id -o tsv >/dev/null
fi

vpn_mutation_started=false
trap - ERR
# Report the established tunnel, preserved public connectivity, and private service reachability.
section "Deployment Summary"
print_kv "Milestone" "reachable: VPN connected"
print_kv "Connection" "$connection_name"
print_kv "Gateway" "$gateway"
print_kv "P2S Address" "$p2s_address"
print_kv "Private Routes" "${private_routes[*]}"
print_kv "Public Default Route" "preserved"
print_kv "Public DNS" "verified"
print_kv "Key Vault" "$([[ $transport == keyvault ]] && echo 'reachable through private endpoint' || echo 'not used')"
print_kv "Next" "$REPO_ROOT/data-pipeline/setup/hil/02-connect-osmo-backend.sh"
info "Optional private reachability is ready"
