#!/usr/bin/env bash
# Configure certificate-authenticated strongSwan IKEv2 access to an Azure VNet.
# cspell:ignore azuregateway addext noout checkend pkey pubin pubout outform strongswan libstrongswan cacerts keyexchange ikev leftfirewall leftcert leftid leftsourceip rightid rightsubnet dpdaction closeaction keyingtries statusall xfrm tcpdump systemd resolvectl
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Acquire VPN inputs, configure certificate-authenticated strongSwan, manage route-only DNS, or report status.
The root CA private key must remain on the external signing system.

OPTIONS:
    -h, --help                    Show this help message
  --download-profile            Download and validate the Azure Generic VPN profile
    --generate-csr                Generate a private key and CSR only
    --install                     Install and start the strongSwan connection
    --status                      Validate the installed connection
  --ensure-connected            Start the connection when needed, then validate it
  --configure-split-dns         Reconcile the script-owned route-only DNS drop-in
  --dns-status                  Validate the script-owned DNS state and queries
  --rollback-split-dns          Remove only verified script-owned DNS state
  --subscription-id ID          Azure subscription containing the VPN gateway
  --resource-group NAME         Resource group containing the VPN gateway
  --vpn-gateway-name NAME       Existing Azure VPN gateway name
  --expected-gateway HOST       Expected VpnServer hostname from the profile
  --profile-dir DIR             Protected external VPN profile output directory
    --connection-name NAME        Connection name (default: physical-ai-azure)
    --gateway HOST                VpnServer value from Azure Generic/VpnSettings.xml
    --azure-vnet-cidr CIDR        Azure route installed through IKEv2
    --p2s-cidr CIDR               Expected Azure VPN client address pool
    --osmo-url URL                Optional private OSMO health endpoint
    --csr-dir DIR                 CSR output directory
    --client-certificate PATH     Signed client certificate PEM
    --client-key PATH             Client private key PEM
    --client-ca-certificate PATH  CA chain that signed the client certificate
    --vpn-server-ca PATH          VpnServerRoot certificate from Azure profile
    --client-ca-sha256 SHA256     Expected client CA certificate SHA-256
    --edge-kubeconfig PATH        Explicit K3s kubeconfig for pod-path validation
    --edge-context NAME           Explicit K3s context for pod-path validation
    --pod-probe                    Prove pod traffic uses the assigned P2S source address
    --dns-server ADDRESS          Private DNS resolver reachable through the VPN
    --private-zone ZONE           Route-only private DNS zone (repeatable)
    --public-canary HOST          Public DNS name that must resolve (default: mcr.microsoft.com)
    --private-host EXPECTATION    Private HOST=ADDRESS_OR_CIDR assertion (repeatable)
    --config-preview              Print configuration and exit

EXAMPLES:
    $(basename "$0") --download-profile --subscription-id <id> --resource-group <rg> \
      --vpn-gateway-name <gateway> --expected-gateway <host> --profile-dir /protected/vpn-profile
    $(basename "$0") --generate-csr --connection-name hil-lab-01
    $(basename "$0") --install --gateway azuregateway.example.vpn.azure.com \\
      --azure-vnet-cidr 10.0.0.0/16 --p2s-cidr 192.168.200.0/24 \\
      --client-certificate /protected/client.pem --client-key /protected/client.key \\
      --client-ca-certificate /protected/client-ca.pem \\
      --vpn-server-ca /protected/VpnServerRoot.pem
EOF
}

mode=""
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
resource_group="${AZURE_RESOURCE_GROUP:-}"
vpn_gateway_name="${VPN_GATEWAY_NAME:-}"
expected_gateway="${VPN_EXPECTED_GATEWAY:-}"
profile_dir="${VPN_PROFILE_DIR:-}"
connection_name="${VPN_CONNECTION_NAME:-physical-ai-azure}"
gateway="${VPN_GATEWAY_HOST:-}"
azure_vnet_cidr="${AZURE_VNET_CIDR:-}"
p2s_cidr="${P2S_CLIENT_CIDR:-}"
osmo_url="${OSMO_PRIVATE_URL:-}"
csr_dir="${VPN_CSR_DIR:-$EDGE_STATE_DIR/vpn-csr}"
client_certificate=""
client_key=""
client_ca_certificate=""
vpn_server_ca=""
client_ca_sha256="${VPN_CLIENT_CA_SHA256:-}"
edge_kubeconfig=""
edge_context=""
pod_probe=false
dns_server="${VPN_DNS_SERVER:-}"
public_canary="${VPN_PUBLIC_DNS_CANARY:-mcr.microsoft.com}"
private_zones=()
private_hosts=()
dns_owner_marker="# Managed by physical-ai-toolchain edge/02-configure-vpn.sh"
dns_dropin="${VPN_DNS_DROPIN:-/etc/systemd/resolved.conf.d/90-physical-ai-azure-private.conf}"
dns_state_dir="$EDGE_STATE_DIR/vpn-dns"
dns_manifest="$dns_state_dir/split-dns-state.json"
dns_transaction_root="$dns_state_dir/transactions"
dns_state_kind=""
dns_current_dropin_sha=""
dns_desired_dropin_sha=""
dns_desired_manifest_sha=""
config_preview=false

select_mode() {
  local selected="${1:?mode required}"
  [[ -z "$mode" ]] || fatal "Select exactly one operation mode"
  mode="$selected"
}

validate_profile_manifest() {
  local directory="${1:?profile directory required}"
  local manifest="$directory/profile-metadata.json"
  local settings="$directory/VpnSettings.xml"
  local server_ca="$directory/VpnServerRoot.pem"
  local expected_settings_sha expected_ca_sha

  require_protected_directory "$directory"
  require_protected_file "$manifest"
  require_protected_file "$settings"
  require_protected_file "$server_ca"
  jq -e --arg subscription "$subscription_id" --arg resource_group "$resource_group" \
    --arg vpn_gateway "$vpn_gateway_name" --arg gateway "$expected_gateway" '
    .schema_version == 1 and
    .kind == "physical-ai-vpn-profile" and
    .subscription_id == $subscription and
    .resource_group == $resource_group and
    .vpn_gateway_name == $vpn_gateway and
    .gateway == $gateway and
    (.files["VpnSettings.xml"] | test("^[0-9a-f]{64}$")) and
    (.files["VpnServerRoot.pem"] | test("^[0-9a-f]{64}$"))
  ' "$manifest" >/dev/null || fatal "Existing VPN profile is not owned by this script for the requested Azure target"
  expected_settings_sha=$(jq -r '.files["VpnSettings.xml"]' "$manifest")
  expected_ca_sha=$(jq -r '.files["VpnServerRoot.pem"]' "$manifest")
  [[ "$(calculate_sha256 "$settings")" == "$expected_settings_sha" ]] || \
    fatal "Existing VPN settings differ from the ownership manifest"
  [[ "$(calculate_sha256 "$server_ca")" == "$expected_ca_sha" ]] || \
    fatal "Existing VPN server CA differs from the ownership manifest"
}

download_vpn_profile() {
  local parent staging extract_dir archive settings_source server_ca_source parsed_gateway
  local settings_sha server_ca_sha backup="" profile_url active_subscription
  local settings_files=() server_ca_files=()

  [[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
  [[ -n "$resource_group" ]] || fatal "--resource-group is required"
  [[ -n "$vpn_gateway_name" ]] || fatal "--vpn-gateway-name is required"
  [[ -n "$expected_gateway" ]] || fatal "--expected-gateway is required"
  [[ "$expected_gateway" =~ ^[A-Za-z0-9.-]+$ ]] || fatal "Invalid expected VPN gateway hostname"
  [[ -n "$profile_dir" ]] || fatal "--profile-dir is required"
  require_external_runtime_path "$profile_dir"

  parent=$(dirname "$profile_dir")
  mkdir -p "$parent"
  [[ ! -L "$parent" ]] || fatal "VPN profile parent must not be a symlink: $parent"
  if [[ -e "$profile_dir" ]]; then
    [[ -d "$profile_dir" && ! -L "$profile_dir" ]] || fatal "VPN profile output must be a non-symlink directory"
    validate_profile_manifest "$profile_dir"
  fi

  az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
  active_subscription=$(az account show --query id -o tsv)
  [[ "$active_subscription" == "$subscription_id" ]] || \
    fatal "Active Azure subscription $active_subscription does not match $subscription_id"
  az network vnet-gateway show --subscription "$subscription_id" --resource-group "$resource_group" \
    --name "$vpn_gateway_name" --query id -o tsv | grep -q '/virtualNetworkGateways/' || \
    fatal "VPN gateway not found: $vpn_gateway_name"
  profile_url=$(az network vnet-gateway vpn-client show-url --subscription "$subscription_id" \
    --resource-group "$resource_group" --name "$vpn_gateway_name" -o tsv)
  printf '%s\n' "$profile_url" | grep -Eq '^https://[^[:space:]"\\]+$' || \
    fatal "Azure did not return a valid HTTPS VPN profile URL"

  umask 077
  staging=$(mktemp -d "${profile_dir}.tmp.XXXXXX")
  chmod 0700 "$staging"
  # shellcheck disable=SC2329  # invoked by the EXIT trap
  cleanup_profile_download() {
    [[ -z "${staging:-}" ]] || rm -rf "$staging"
  }
  trap cleanup_profile_download EXIT
  archive="$staging/vpnclientconfiguration.zip"
  extract_dir="$staging/extracted"
  # pinning-ignore: Azure generates this authenticated profile dynamically and publishes no stable digest.
  printf 'url = "%s"\n' "$profile_url" | curl --fail --silent --show-error --location --config - --output "$archive"
  unset profile_url
  unzip -q "$archive" -d "$extract_dir"

  while IFS= read -r settings_source; do
    settings_files+=("$settings_source")
  done < <(find "$extract_dir/Generic" -maxdepth 1 -type f -name 'VpnSettings.xml' -print)
  while IFS= read -r server_ca_source; do
    server_ca_files+=("$server_ca_source")
  done < <(find "$extract_dir/Generic" -maxdepth 1 -type f -name 'VpnServerRoot.cer*' -print)
  (( ${#settings_files[@]} == 1 )) || fatal "Azure Generic profile must contain exactly one VpnSettings.xml"
  (( ${#server_ca_files[@]} == 1 )) || fatal "Azure Generic profile must contain exactly one VpnServerRoot certificate"
  settings_source="${settings_files[0]}"
  server_ca_source="${server_ca_files[0]}"
  parsed_gateway=$(python3 - "$settings_source" <<'PYTHON'
import sys
import xml.etree.ElementTree as ET

gateway = ET.parse(sys.argv[1]).getroot().findtext("VpnServer")
if not gateway:
    raise SystemExit("VpnSettings.xml does not contain VpnServer")
print(gateway.strip())
PYTHON
)
  [[ "$parsed_gateway" == "$expected_gateway" ]] || \
    fatal "Azure VPN profile gateway $parsed_gateway does not match $expected_gateway"

  install -m 0600 "$settings_source" "$staging/VpnSettings.xml"
  if ! openssl x509 -inform DER -in "$server_ca_source" -out "$staging/VpnServerRoot.pem" 2>/dev/null; then
    openssl x509 -in "$server_ca_source" -out "$staging/VpnServerRoot.pem"
  fi
  chmod 0600 "$staging/VpnServerRoot.pem"
  openssl x509 -in "$staging/VpnServerRoot.pem" -noout -subject -issuer >/dev/null
  settings_sha=$(calculate_sha256 "$staging/VpnSettings.xml")
  server_ca_sha=$(calculate_sha256 "$staging/VpnServerRoot.pem")
  jq -n --arg subscription_id "$subscription_id" --arg resource_group "$resource_group" \
    --arg vpn_gateway_name "$vpn_gateway_name" --arg gateway "$parsed_gateway" \
    --arg settings_sha "$settings_sha" --arg server_ca_sha "$server_ca_sha" \
    '{schema_version: 1, kind: "physical-ai-vpn-profile", subscription_id: $subscription_id, resource_group: $resource_group, vpn_gateway_name: $vpn_gateway_name, gateway: $gateway, files: {"VpnSettings.xml": $settings_sha, "VpnServerRoot.pem": $server_ca_sha}}' \
    > "$staging/profile-metadata.json"
  chmod 0600 "$staging/profile-metadata.json"
  rm -rf "$archive" "$extract_dir"

  if [[ -d "$profile_dir" ]]; then
    backup=$(mktemp -d "${profile_dir}.backup.XXXXXX")
    rmdir "$backup"
    mv "$profile_dir" "$backup"
  fi
  if mv "$staging" "$profile_dir"; then
    staging=""
    [[ -z "$backup" ]] || rm -rf "$backup"
  else
    [[ -z "$backup" ]] || mv "$backup" "$profile_dir"
    fatal "Unable to install the validated VPN profile"
  fi
  trap - EXIT
  validate_profile_manifest "$profile_dir"

  section "Deployment Summary"
  print_kv "VPN Gateway" "$vpn_gateway_name"
  print_kv "Gateway Host" "$parsed_gateway"
  print_kv "Profile Directory" "$profile_dir"
  print_kv "Server CA" "$profile_dir/VpnServerRoot.pem"
  info "Azure VPN profile downloaded and validated"
}

normalize_private_zones() {
  local zone normalized
  local normalized_zones=()

  (( ${#private_zones[@]} > 0 )) || fatal "At least one --private-zone is required"
  for zone in "${private_zones[@]}"; do
    normalized="${zone#~}"
    [[ "$normalized" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || fatal "Invalid private DNS zone: $zone"
    normalized_zones+=("$normalized")
  done
  mapfile -t private_zones < <(printf '%s\n' "${normalized_zones[@]}" | LC_ALL=C sort -u)
}

validate_private_host_inputs() {
  local expectation host expected
  for expectation in "${private_hosts[@]}"; do
    [[ "$expectation" == *=* ]] || fatal "--private-host must use HOST=ADDRESS_OR_CIDR"
    host="${expectation%%=*}"
    expected="${expectation#*=}"
    [[ "$host" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || fatal "Invalid private hostname: $host"
    python3 - "$expected" <<'PYTHON'
import ipaddress
import sys

value = sys.argv[1]
try:
    ipaddress.ip_address(value)
except ValueError:
    ipaddress.ip_network(value, strict=False)
PYTHON
  done
}

validate_dns_queries() {
  local expectation host expected output
  resolvectl query "$public_canary" >/dev/null 2>&1 || {
    error "Public DNS canary is unresolved: $public_canary"
    return 1
  }
  for expectation in "${private_hosts[@]}"; do
    host="${expectation%%=*}"
    expected="${expectation#*=}"
    output=$(resolvectl query --legend=no "$host" 2>/dev/null) || {
      error "Private DNS name is unresolved: $host"
      return 1
    }
    python3 - "$host" "$expected" "$output" <<'PYTHON' || return 1
import ipaddress
import re
import sys

host, expected, output = sys.argv[1:]
addresses = []
for token in re.findall(r"[0-9A-Fa-f:.]+", output):
    try:
        addresses.append(ipaddress.ip_address(token.rstrip(".")))
    except ValueError:
        continue
try:
    expected_address = ipaddress.ip_address(expected)
except ValueError:
    network = ipaddress.ip_network(expected, strict=False)
    matched = any(address in network for address in addresses)
else:
    matched = expected_address in addresses
if not matched:
    raise SystemExit(f"{host} did not resolve to {expected}")
PYTHON
  done
}

root_file_sha256() {
  sudo sha256sum "${1:?file required}" | awk '{print $1}'
}

load_dns_owned_state() {
  dns_state_kind=""
  dns_current_dropin_sha=""
  if ! sudo test -e "$dns_dropin" && ! sudo test -e "$dns_manifest"; then
    dns_state_kind="absent"
    return
  fi
  if ! sudo test -f "$dns_dropin" || ! sudo test -f "$dns_manifest"; then
    fatal "Split-DNS state is incomplete; inspect it manually before continuing"
  fi
  sudo grep -Fqx "$dns_owner_marker" "$dns_dropin" || \
    fatal "Split-DNS drop-in is not owned by this script: $dns_dropin"
  dns_current_dropin_sha=$(root_file_sha256 "$dns_dropin")
  sudo cat "$dns_manifest" | jq -e --arg target "$dns_dropin" --arg digest "$dns_current_dropin_sha" '
    .schema_version == 1 and
    .kind == "physical-ai-split-dns" and
    .target == $target and
    .dropin_sha256 == $digest
  ' >/dev/null || fatal "Split-DNS state has drifted from its ownership manifest"
  dns_state_kind="owned"
}

write_dns_desired_state() {
  local work_dir="${1:?work directory required}" zone_args=() zone
  for zone in "${private_zones[@]}"; do
    zone_args+=("~$zone")
  done
  cat > "$work_dir/dropin.conf" <<EOF
$dns_owner_marker
[Resolve]
DNS=$dns_server
Domains=${zone_args[*]}
EOF
  dns_desired_dropin_sha=$(calculate_sha256 "$work_dir/dropin.conf")
  jq -n --arg target "$dns_dropin" --arg digest "$dns_desired_dropin_sha" \
    --arg dns_server "$dns_server" --argjson zones "$(printf '%s\n' "${private_zones[@]}" | jq -R . | jq -s .)" \
    '{schema_version: 1, kind: "physical-ai-split-dns", target: $target, dropin_sha256: $digest, dns_server: $dns_server, private_zones: $zones}' \
    > "$work_dir/manifest.json"
  dns_desired_manifest_sha=$(calculate_sha256 "$work_dir/manifest.json")
}

restore_dns_transaction() {
  local transaction="${1:?transaction directory required}" dropin_installed="${2:?drop-in state required}"
  local manifest_installed="${3:?manifest state required}" current_sha restore_error=""
  local prior_present=false expected_sha

  sudo test -f "$transaction/prior-present" && prior_present=true

  if [[ "$dropin_installed" == "true" || "$prior_present" == "true" ]]; then
    if [[ "$dropin_installed" == "true" ]]; then
      expected_sha="$dns_desired_dropin_sha"
    else
      expected_sha=$(root_file_sha256 "$transaction/dropin.conf")
    fi
    sudo test -f "$dns_dropin" || restore_error="DNS drop-in is missing before restoration"
    if [[ -z "$restore_error" ]]; then
      current_sha=$(root_file_sha256 "$dns_dropin")
      [[ "$current_sha" == "$expected_sha" ]] || restore_error="DNS drop-in changed concurrently"
    fi
  elif sudo test -e "$dns_dropin"; then
    restore_error="DNS drop-in appeared concurrently"
  fi

  if [[ -z "$restore_error" && ( "$manifest_installed" == "true" || "$prior_present" == "true" ) ]]; then
    if [[ "$manifest_installed" == "true" ]]; then
      expected_sha="$dns_desired_manifest_sha"
    else
      expected_sha=$(root_file_sha256 "$transaction/manifest.json")
    fi
    sudo test -f "$dns_manifest" || restore_error="DNS manifest is missing before restoration"
    if [[ -z "$restore_error" ]]; then
      current_sha=$(root_file_sha256 "$dns_manifest")
      [[ "$current_sha" == "$expected_sha" ]] || restore_error="DNS manifest changed concurrently"
    fi
  elif [[ -z "$restore_error" ]] && sudo test -e "$dns_manifest"; then
    restore_error="DNS manifest appeared concurrently"
  fi
  [[ -z "$restore_error" ]] || {
    error "$restore_error"
    return 1
  }

  if [[ "$prior_present" == "true" ]]; then
    sudo install -m 0644 "$transaction/dropin.conf" "$dns_dropin" || return 1
    sudo install -m 0600 "$transaction/manifest.json" "$dns_manifest" || return 1
  else
    sudo rm -f "$dns_dropin" "$dns_manifest" || return 1
  fi
  sudo systemctl restart systemd-resolved || return 1
  resolvectl query "$public_canary" >/dev/null 2>&1 || return 1
}

configure_split_dns() {
  local work_dir transaction failure="" dropin_installed=false manifest_installed=false

  [[ "$(uname -s)" == "Linux" ]] || fatal "Split-DNS configuration supports Linux only"
  require_tools install sha256sum sudo systemctl
  require_external_runtime_path "$dns_state_dir"
  require_external_runtime_path "$dns_dropin"
  is_rfc1918_ipv4 "$dns_server" || fatal "--dns-server must be an RFC1918 IPv4 address"
  normalize_private_zones
  validate_private_host_inputs
  load_dns_owned_state

  umask 077
  work_dir=$(mktemp -d)
  chmod 0700 "$work_dir"
  write_dns_desired_state "$work_dir"
  if [[ "$dns_state_kind" == "owned" && "$dns_current_dropin_sha" == "$dns_desired_dropin_sha" ]]; then
    validate_dns_queries || fatal "Split-DNS queries failed"
    rm -rf "$work_dir"
    section "Deployment Summary"
    print_kv "DNS State" "unchanged"
    print_kv "DNS Server" "$dns_server"
    print_kv "Private Zones" "${private_zones[*]}"
    info "Split-DNS configuration already matches the requested state"
    return
  fi

  sudo install -d -m 0700 "$dns_state_dir" "$dns_transaction_root"
  transaction=$(sudo mktemp -d "$dns_transaction_root/configure.XXXXXX")
  sudo chmod 0700 "$transaction"
  if [[ "$dns_state_kind" == "owned" ]]; then
    sudo cp -p "$dns_dropin" "$transaction/dropin.conf"
    sudo cp -p "$dns_manifest" "$transaction/manifest.json"
    sudo touch "$transaction/prior-present"
    sudo chmod 0600 "$transaction/prior-present"
  else
    sudo touch "$transaction/prior-absent"
    sudo chmod 0600 "$transaction/prior-absent"
  fi

  sudo install -d -m 0755 "$(dirname "$dns_dropin")"
  if sudo install -m 0644 "$work_dir/dropin.conf" "$dns_dropin"; then
    dropin_installed=true
  else
    failure="Unable to install the split-DNS drop-in"
  fi
  if [[ -z "$failure" ]]; then
    if sudo install -m 0600 "$work_dir/manifest.json" "$dns_manifest"; then
      manifest_installed=true
    else
      failure="Unable to install the split-DNS ownership manifest"
    fi
  fi
  [[ -n "$failure" ]] || sudo systemctl restart systemd-resolved || failure="Unable to restart systemd-resolved"
  [[ -n "$failure" ]] || validate_dns_queries || failure="Split-DNS post-install validation failed"

  if [[ -n "$failure" ]]; then
    if restore_dns_transaction "$transaction" "$dropin_installed" "$manifest_installed"; then
      sudo rm -rf "$transaction"
      rm -rf "$work_dir"
      fatal "$failure; the prior DNS state was restored"
    fi
    rm -rf "$work_dir"
    error "$failure"
    fatal "DNS restoration also failed; protected recovery state remains at $transaction"
  fi

  sudo rm -rf "$transaction"
  rm -rf "$work_dir"
  section "Deployment Summary"
  print_kv "DNS State" "configured"
  print_kv "DNS Server" "$dns_server"
  print_kv "Private Zones" "${private_zones[*]}"
  print_kv "Public Canary" "$public_canary"
  info "Route-only split DNS configured"
}

report_dns_status() {
  [[ "$(uname -s)" == "Linux" ]] || fatal "Split-DNS status supports Linux only"
  require_tools sha256sum sudo
  require_external_runtime_path "$dns_state_dir"
  require_external_runtime_path "$dns_dropin"
  validate_private_host_inputs
  load_dns_owned_state
  [[ "$dns_state_kind" == "owned" ]] || fatal "Split-DNS state is not configured"
  validate_dns_queries || fatal "Split-DNS validation failed"
  section "Deployment Summary"
  print_kv "DNS State" "verified"
  print_kv "Public Canary" "$public_canary"
  print_kv "Private Hosts" "${private_hosts[*]:-not requested}"
  info "Split-DNS validation passed"
}

rollback_split_dns() {
  local transaction failure="" current_sha expected_sha

  [[ "$(uname -s)" == "Linux" ]] || fatal "Split-DNS rollback supports Linux only"
  require_tools sha256sum sudo systemctl
  require_external_runtime_path "$dns_state_dir"
  require_external_runtime_path "$dns_dropin"
  load_dns_owned_state
  if [[ "$dns_state_kind" == "absent" ]]; then
    resolvectl query "$public_canary" >/dev/null 2>&1 || fatal "Public DNS canary is unresolved: $public_canary"
    section "Deployment Summary"
    print_kv "DNS State" "already absent"
    info "No script-owned split-DNS state is installed"
    return
  fi

  sudo install -d -m 0700 "$dns_state_dir" "$dns_transaction_root"
  transaction=$(sudo mktemp -d "$dns_transaction_root/rollback.XXXXXX")
  sudo chmod 0700 "$transaction"
  sudo cp -p "$dns_dropin" "$transaction/dropin.conf"
  sudo cp -p "$dns_manifest" "$transaction/manifest.json"
  sudo touch "$transaction/prior-present"
  sudo chmod 0600 "$transaction/prior-present"
  sudo rm -f "$dns_dropin" "$dns_manifest" || failure="Unable to remove all script-owned split-DNS state"
  [[ -n "$failure" ]] || sudo systemctl restart systemd-resolved || failure="Unable to restart systemd-resolved after rollback"
  [[ -n "$failure" ]] || resolvectl query "$public_canary" >/dev/null 2>&1 || \
    failure="Public DNS validation failed after rollback"
  if [[ -n "$failure" ]]; then
    expected_sha=$(root_file_sha256 "$transaction/dropin.conf")
    if sudo test -e "$dns_dropin"; then
      current_sha=$(root_file_sha256 "$dns_dropin")
      [[ "$current_sha" == "$expected_sha" ]] || \
        fatal "$failure; DNS drop-in changed concurrently and recovery state remains at $transaction"
    fi
    expected_sha=$(root_file_sha256 "$transaction/manifest.json")
    if sudo test -e "$dns_manifest"; then
      current_sha=$(root_file_sha256 "$dns_manifest")
      [[ "$current_sha" == "$expected_sha" ]] || \
        fatal "$failure; DNS manifest changed concurrently and recovery state remains at $transaction"
    fi
    if sudo install -m 0644 "$transaction/dropin.conf" "$dns_dropin" && \
      sudo install -m 0600 "$transaction/manifest.json" "$dns_manifest" && \
      sudo systemctl restart systemd-resolved && resolvectl query "$public_canary" >/dev/null 2>&1; then
      sudo rm -rf "$transaction"
      fatal "$failure; the prior DNS state was restored"
    fi
    error "$failure"
    fatal "DNS rollback restoration also failed; protected recovery state remains at $transaction"
  fi
  sudo rm -rf "$transaction"
  section "Deployment Summary"
  print_kv "DNS State" "removed"
  print_kv "Public Canary" "$public_canary"
  info "Script-owned split DNS rolled back"
}

validate_vpn_connection() {
  local p2s_address status_output route route_device osmo_ip capture_file capture_pid packet_summary
  local probe_namespace probe_passed
  local assigned_addresses=()

  [[ -n "$azure_vnet_cidr" ]] || fatal "--azure-vnet-cidr is required"
  [[ -n "$p2s_cidr" ]] || fatal "--p2s-cidr is required"
  if [[ "$pod_probe" == "true" ]]; then
    [[ -n "$edge_kubeconfig" && -n "$edge_context" ]] || \
      fatal "--pod-probe requires --edge-kubeconfig and --edge-context"
  fi
  ipsec status "$connection_name" | grep -q 'ESTABLISHED' || fatal "IKEv2 connection is not established: $connection_name"
  mapfile -t assigned_addresses < <(ip -o -4 addr show scope global | awk '{print $4}')
  p2s_address=$(python3 - "$p2s_cidr" "${assigned_addresses[@]}" <<'PYTHON'
import ipaddress
import sys

pool = ipaddress.ip_network(sys.argv[1], strict=False)
addresses = [ipaddress.ip_interface(value).ip for value in sys.argv[2:]]
matches = [address for address in addresses if address in pool]
if not matches:
    raise SystemExit(f"no assigned address belongs to {pool}")
print(matches[0])
PYTHON
)
  status_output=$(ipsec statusall "$connection_name")
  grep -F "$p2s_address" <<< "$status_output" >/dev/null || \
    fatal "Negotiated IKEv2 state does not contain assigned P2S address $p2s_address"
  grep -F "$azure_vnet_cidr" <<< "$status_output" >/dev/null || \
    fatal "Negotiated IKEv2 state does not contain Azure traffic selector $azure_vnet_cidr"
  ip xfrm policy | grep -F "dst $azure_vnet_cidr" >/dev/null || fatal "No XFRM policy protects Azure VNet $azure_vnet_cidr"
  route=$(ip route get "$(python3 - "$azure_vnet_cidr" <<'PYTHON'
import ipaddress
import sys
print(next(ipaddress.ip_network(sys.argv[1], strict=False).hosts()))
PYTHON
)" | head -1)
  [[ -n "$route" ]] || fatal "No route to Azure VNet $azure_vnet_cidr"
  route_device=$(awk '{for (i = 1; i <= NF; i++) if ($i == "dev") {print $(i+1); exit}}' <<< "$route")
  [[ -n "$route_device" ]] || fatal "Azure route does not identify an egress interface"
  if [[ -n "$osmo_url" ]]; then
    require_tools curl
    [[ "$osmo_url" == http://10.* || "$osmo_url" == http://192.168.* || "$osmo_url" =~ ^http://172\.(1[6-9]|2[0-9]|3[01])\. ]] || \
      fatal "Private lab OSMO URL must use an RFC1918 address"
    curl -fsS --connect-timeout 5 "${osmo_url%/}/api/version" >/dev/null || fatal "OSMO endpoint is unreachable: $osmo_url"
  fi

  if [[ "$pod_probe" == "true" ]]; then
    require_tools tcpdump timeout
    verify_kube_target "$edge_kubeconfig" "$edge_context" k3s
    [[ -n "$osmo_url" ]] || fatal "--pod-probe requires --osmo-url"
    osmo_ip="${osmo_url#http://}"
    is_rfc1918_ipv4 "$osmo_ip" || fatal "--pod-probe requires --osmo-url with an RFC1918 IPv4 address"
    capture_file=$(mktemp)
    probe_namespace="physical-ai-vpn-smoke"
    ensure_namespace "$edge_kubeconfig" "$edge_context" "$probe_namespace"
    cleanup_probe() {
      kube_kubectl "$edge_kubeconfig" "$edge_context" delete namespace "$probe_namespace" \
        --ignore-not-found --wait=true >/dev/null 2>&1 || true
      rm -f "$capture_file"
    }
    trap cleanup_probe EXIT
    sudo timeout 30 tcpdump -nn -i any -c 20 -w "$capture_file" \
      "dst host $osmo_ip and tcp dst port 80" >/dev/null 2>&1 &
    capture_pid=$!
    sleep 1
    kube_kubectl "$edge_kubeconfig" "$edge_context" delete pod vpn-egress-smoke \
      -n "$probe_namespace" --ignore-not-found >/dev/null
    kube_kubectl "$edge_kubeconfig" "$edge_context" run vpn-egress-smoke \
      -n "$probe_namespace" --restart=Never \
      --image=alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1 \
      --command -- sh -ceu "wget -qO- '${osmo_url%/}/api/version' >/dev/null"
    probe_passed=true
    kube_kubectl "$edge_kubeconfig" "$edge_context" wait pod/vpn-egress-smoke \
      -n "$probe_namespace" --for=jsonpath='{.status.phase}'=Succeeded --timeout=60s >/dev/null || probe_passed=false
    wait "$capture_pid" || true
    packet_summary=$(sudo tcpdump -nn -r "$capture_file" 2>/dev/null)
    grep -F "IP ${p2s_address}." <<< "$packet_summary" >/dev/null || probe_passed=false
    cleanup_probe
    trap - EXIT
    [[ "$probe_passed" == "true" ]] || fatal "Pod-to-OSMO traffic did not complete with P2S source address $p2s_address"
  fi

  section "Deployment Summary"
  print_kv "Connection" "$connection_name"
  print_kv "State" "established"
  print_kv "P2S Address" "$p2s_address"
  print_kv "Azure Route" "$route"
  print_kv "Pod Egress" "$([[ $pod_probe == true ]] && echo verified || echo 'not requested')"
  print_kv "OSMO" "${osmo_url:-not checked}"
  info "VPN validation passed"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --download-profile)     select_mode "download-profile"; shift ;;
    --generate-csr)         select_mode "generate-csr"; shift ;;
    --install)              select_mode "install"; shift ;;
    --status)               select_mode "status"; shift ;;
    --ensure-connected)     select_mode "ensure-connected"; shift ;;
    --configure-split-dns)  select_mode "configure-split-dns"; shift ;;
    --dns-status)           select_mode "dns-status"; shift ;;
    --rollback-split-dns)   select_mode "rollback-split-dns"; shift ;;
    --subscription-id)      subscription_id="$2"; shift 2 ;;
    --resource-group)       resource_group="$2"; shift 2 ;;
    --vpn-gateway-name)     vpn_gateway_name="$2"; shift 2 ;;
    --expected-gateway)     expected_gateway="$2"; shift 2 ;;
    --profile-dir)          profile_dir="$2"; shift 2 ;;
    --connection-name)      connection_name="$2"; shift 2 ;;
    --gateway)              gateway="$2"; shift 2 ;;
    --azure-vnet-cidr)      azure_vnet_cidr="$2"; shift 2 ;;
    --p2s-cidr)             p2s_cidr="$2"; shift 2 ;;
    --osmo-url)             osmo_url="$2"; shift 2 ;;
    --csr-dir)              csr_dir="$2"; shift 2 ;;
    --client-certificate)   client_certificate="$2"; shift 2 ;;
    --client-key)           client_key="$2"; shift 2 ;;
    --client-ca-certificate) client_ca_certificate="$2"; shift 2 ;;
    --vpn-server-ca)        vpn_server_ca="$2"; shift 2 ;;
    --client-ca-sha256)     client_ca_sha256="$2"; shift 2 ;;
    --edge-kubeconfig)      edge_kubeconfig="$2"; shift 2 ;;
    --edge-context)         edge_context="$2"; shift 2 ;;
    --pod-probe)            pod_probe=true; shift ;;
    --dns-server)           dns_server="$2"; shift 2 ;;
    --private-zone)         private_zones+=("$2"); shift 2 ;;
    --public-canary)        public_canary="$2"; shift 2 ;;
    --private-host)         private_hosts+=("$2"); shift 2 ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$mode" ]] || fatal "Select exactly one operation mode"
[[ "$connection_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ ]] || fatal "Invalid connection name: $connection_name"

case "$mode" in
  download-profile) require_tools az curl jq openssl python3 unzip ;;
  generate-csr|install) require_tools openssl ;;
  status|ensure-connected) require_tools ip ipsec python3 ;;
  configure-split-dns|dns-status|rollback-split-dns) require_tools jq python3 resolvectl ;;
esac

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Mode" "$mode"
  case "$mode" in
    download-profile)
      print_kv "Subscription" "${subscription_id:-<required>}"
      print_kv "Resource Group" "${resource_group:-<required>}"
      print_kv "VPN Gateway" "${vpn_gateway_name:-<required>}"
      print_kv "Expected Host" "${expected_gateway:-<required>}"
      print_kv "Profile Directory" "${profile_dir:-<required>}"
      ;;
    configure-split-dns|dns-status|rollback-split-dns)
      print_kv "DNS Server" "${dns_server:-<required for configure>}"
      print_kv "Private Zones" "${private_zones[*]:-not configured}"
      print_kv "Public Canary" "$public_canary"
      print_kv "Private Hosts" "${private_hosts[*]:-not configured}"
      ;;
    *)
      print_kv "Connection" "$connection_name"
      print_kv "Gateway" "${gateway:-<required for install>}"
      print_kv "Azure VNet CIDR" "${azure_vnet_cidr:-<required for install/status>}"
      print_kv "P2S CIDR" "${p2s_cidr:-<required for status>}"
      print_kv "OSMO URL" "${osmo_url:-not configured}"
      print_kv "CSR Directory" "$csr_dir"
      print_kv "Client Certificate" "${client_certificate:-<required for install>}"
      print_kv "Client Key" "${client_key:-<required for install>}"
      print_kv "Client CA" "${client_ca_certificate:-<required for install>}"
      print_kv "VPN Server CA" "${vpn_server_ca:-<required for install>}"
      print_kv "Pod Probe" "$pod_probe"
      ;;
  esac
  exit 0
fi

case "$mode" in
  download-profile)
    download_vpn_profile
    exit 0
    ;;
  configure-split-dns)
    [[ -n "$dns_server" ]] || fatal "--dns-server is required for split-DNS configuration"
    configure_split_dns
    exit 0
    ;;
  dns-status)
    report_dns_status
    exit 0
    ;;
  rollback-split-dns)
    rollback_split_dns
    exit 0
    ;;
  status)
    validate_vpn_connection
    exit 0
    ;;
  ensure-connected)
    require_tools sudo
    if ! ipsec status "$connection_name" | grep -q 'ESTABLISHED'; then
      sudo ipsec up "$connection_name"
    fi
    validate_vpn_connection
    exit 0
    ;;
esac

if [[ "$mode" == "generate-csr" ]]; then
  install -d -m 0700 "$csr_dir"
  client_key="$csr_dir/${connection_name}.key"
  csr_file="$csr_dir/${connection_name}.csr"
  [[ ! -e "$client_key" && ! -e "$csr_file" ]] || fatal "CSR material already exists in $csr_dir"
  openssl genrsa -out "$client_key" 3072 >/dev/null 2>&1
  chmod 0600 "$client_key"
  openssl req -new -key "$client_key" -out "$csr_file" -subj "/CN=$connection_name" \
    -addext "extendedKeyUsage=clientAuth"
  chmod 0600 "$csr_file"

  section "Deployment Summary"
  print_kv "Connection" "$connection_name"
  print_kv "Private Key" "$client_key"
  print_kv "CSR" "$csr_file"
  print_kv "CA Handoff" "Sign the CSR externally and return the client and CA certificates"
  info "VPN CSR generated; the private key remains on this host"
  exit 0
fi

[[ -n "$azure_vnet_cidr" ]] || fatal "--azure-vnet-cidr is required"

[[ -n "$gateway" ]] || fatal "--gateway is required for install"
[[ -n "$client_certificate" ]] || fatal "--client-certificate is required for install"
[[ -n "$client_key" ]] || fatal "--client-key is required for install"
[[ -n "$client_ca_certificate" ]] || fatal "--client-ca-certificate is required for install"
[[ -n "$vpn_server_ca" ]] || fatal "--vpn-server-ca is required for install"
require_protected_file "$client_certificate"
require_protected_file "$client_key"
require_protected_file "$client_ca_certificate"
require_protected_file "$vpn_server_ca"

openssl verify -CAfile "$client_ca_certificate" "$client_certificate" >/dev/null || fatal "Client certificate does not chain to the supplied client CA"
openssl x509 -in "$client_certificate" -noout -checkend 86400 >/dev/null || fatal "Client certificate expires within 24 hours"
openssl x509 -in "$client_certificate" -noout -text | grep -A2 'Basic Constraints' | grep -q 'CA:FALSE' || \
  fatal "Client certificate must have CA:FALSE"
openssl x509 -in "$client_certificate" -noout -text | grep -A2 'Extended Key Usage' | grep -q 'TLS Web Client Authentication' || \
  fatal "Client certificate does not contain the clientAuth extended key usage"
cert_key_hash=$(openssl x509 -in "$client_certificate" -pubkey -noout | openssl pkey -pubin -outform der | openssl sha256)
private_key_hash=$(openssl pkey -in "$client_key" -pubout -outform der | openssl sha256)
[[ "$cert_key_hash" == "$private_key_hash" ]] || fatal "Client certificate does not match the private key"
openssl x509 -in "$client_certificate" -noout -subject | grep -Fq "CN = $connection_name" || \
  fatal "Client certificate subject does not match connection name $connection_name"

if [[ -n "$client_ca_sha256" ]]; then
  actual_ca_sha256=$(openssl x509 -in "$client_ca_certificate" -outform der | openssl sha256 | awk '{print $2}')
  [[ "$(printf '%s' "$actual_ca_sha256" | tr '[:upper:]' '[:lower:]')" == \
    "$(printf '%s' "$client_ca_sha256" | tr '[:upper:]' '[:lower:]')" ]] || \
    fatal "Client CA SHA-256 does not match --client-ca-sha256"
fi

[[ "$(uname -s)" == "Linux" ]] || fatal "VPN installation supports Ubuntu Linux only"
require_tools apt-get install sudo
require_tools ip
default_route_before=$(ip -4 route show default)
sudo apt-get update
sudo apt-get install -y strongswan strongswan-pki libstrongswan-extra-plugins tcpdump

state_dir="$EDGE_STATE_DIR/vpn"
sudo install -d -m 0700 "$state_dir" /etc/ipsec.d/cacerts /etc/ipsec.d/certs /etc/ipsec.d/private
sudo install -m 0600 "$client_ca_certificate" "/etc/ipsec.d/cacerts/${connection_name}-client-ca.pem"
sudo install -m 0600 "$vpn_server_ca" "/etc/ipsec.d/cacerts/${connection_name}-server-ca.pem"
sudo install -m 0600 "$client_certificate" "/etc/ipsec.d/certs/${connection_name}.pem"
sudo install -m 0600 "$client_key" "/etc/ipsec.d/private/${connection_name}.key"

tmp_config=$(mktemp)
cat > "$tmp_config" <<EOF
conn $connection_name
    keyexchange=ikev2
    type=tunnel
    leftfirewall=yes
    left=%any
    leftcert=${connection_name}.pem
    leftauth=pubkey
    leftid=%$connection_name
    leftsourceip=%config
    right=$gateway
    rightid=%$gateway
    rightsubnet=$azure_vnet_cidr
    rightauth=pubkey
    auto=start
    dpdaction=restart
    closeaction=restart
    keyingtries=%forever
    esp=aes256gcm16
EOF
sudo install -m 0600 "$tmp_config" "/etc/ipsec.d/${connection_name}.conf"
rm -f "$tmp_config"

if ! sudo grep -Fqx 'include /etc/ipsec.d/*.conf' /etc/ipsec.conf; then
  sudo cp -p /etc/ipsec.conf "$state_dir/ipsec.conf.backup"
  printf '\ninclude /etc/ipsec.d/*.conf\n' | sudo tee -a /etc/ipsec.conf >/dev/null
fi
secret_line=": RSA ${connection_name}.key"
if ! sudo grep -Fqx "$secret_line" /etc/ipsec.secrets; then
  sudo cp -p /etc/ipsec.secrets "$state_dir/ipsec.secrets.backup"
  printf '%s\n' "$secret_line" | sudo tee -a /etc/ipsec.secrets >/dev/null
fi
sudo chmod 0600 /etc/ipsec.secrets
sudo ipsec restart
sudo ipsec up "$connection_name"
default_route_after=$(ip -4 route show default)
[[ "$default_route_after" == "$default_route_before" ]] || fatal "VPN setup changed the Internet default route"

section "Deployment Summary"
print_kv "Connection" "$connection_name"
print_kv "Gateway" "$gateway"
print_kv "Azure VNet CIDR" "$azure_vnet_cidr"
print_kv "Authentication" "certificate"
print_kv "Protocol" "IKEv2"
info "strongSwan VPN configuration complete"
