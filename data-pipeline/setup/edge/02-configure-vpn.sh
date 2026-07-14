#!/usr/bin/env bash
# Configure certificate-authenticated strongSwan IKEv2 access to an Azure VNet.
# cspell:ignore azuregateway addext noout checkend pkey pubin pubout outform strongswan libstrongswan cacerts keyexchange ikev leftfirewall leftcert leftid leftsourceip rightid rightsubnet dpdaction closeaction keyingtries statusall xfrm tcpdump
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

Generate a VPN client CSR, install a returned certificate, or report status.
The root CA private key must remain on the external signing system.

OPTIONS:
    -h, --help                    Show this help message
    --generate-csr                Generate a private key and CSR only
    --install                     Install and start the strongSwan connection
    --status                      Validate the installed connection
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
    --config-preview              Print configuration and exit

EXAMPLES:
    $(basename "$0") --generate-csr --connection-name hil-lab-01
    $(basename "$0") --install --gateway azuregateway.example.vpn.azure.com \\
      --azure-vnet-cidr 10.0.0.0/16 --p2s-cidr 192.168.200.0/24 \\
      --client-certificate /protected/client.pem --client-key /protected/client.key \\
      --client-ca-certificate /protected/client-ca.pem \\
      --vpn-server-ca /protected/VpnServerRoot.pem
EOF
}

mode=""
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
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --generate-csr)         mode="generate-csr"; shift ;;
    --install)              mode="install"; shift ;;
    --status)               mode="status"; shift ;;
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
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$mode" ]] || fatal "Select exactly one of --generate-csr, --install, or --status"
[[ "$connection_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]+$ ]] || fatal "Invalid connection name: $connection_name"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Mode" "$mode"
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
  exit 0
fi

require_tools openssl

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

if [[ "$mode" == "status" ]]; then
  require_tools ip ipsec python3
  [[ -n "$p2s_cidr" ]] || fatal "--p2s-cidr is required for status"
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
  exit 0
fi

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
