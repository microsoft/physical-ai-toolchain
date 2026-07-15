#!/usr/bin/env bash
# Validate an Ubuntu host before VPN, K3s, Arc, or HiL setup.
# cspell:ignore crio microk inodes timedatectl firewalld nftables
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

Validate Ubuntu host capacity, networking, runtime ownership, and CIDR safety.

OPTIONS:
    -h, --help               Show this help message
    --azure-vnet-cidr CIDR   Azure VNet CIDR (required)
    --p2s-cidr CIDR          VPN client address pool CIDR (required)
    --pod-cidr CIDR          K3s pod CIDR (default: $EDGE_K3S_POD_CIDR)
    --service-cidr CIDR      K3s service CIDR (default: $EDGE_K3S_SERVICE_CIDR)
    --lan-cidr CIDR          LAN CIDR override; repeat for multiple networks
    --inventory PATH         Write non-secret inventory JSON to PATH
    --allow-battery          Permit a host currently using battery power
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --azure-vnet-cidr 10.0.0.0/16 --p2s-cidr 192.168.200.0/24
EOF
}

azure_vnet_cidr="${AZURE_VNET_CIDR:-}"
p2s_cidr="${P2S_CLIENT_CIDR:-}"
pod_cidr="$EDGE_K3S_POD_CIDR"
service_cidr="$EDGE_K3S_SERVICE_CIDR"
inventory="${EDGE_INVENTORY_FILE:-$EDGE_STATE_DIR/edge-inventory.json}"
allow_battery=false
config_preview=false
lan_cidrs=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --azure-vnet-cidr)  azure_vnet_cidr="$2"; shift 2 ;;
    --p2s-cidr)         p2s_cidr="$2"; shift 2 ;;
    --pod-cidr)         pod_cidr="$2"; shift 2 ;;
    --service-cidr)     service_cidr="$2"; shift 2 ;;
    --lan-cidr)         lan_cidrs+=("$2"); shift 2 ;;
    --inventory)        inventory="$2"; shift 2 ;;
    --allow-battery)    allow_battery=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$azure_vnet_cidr" ]] || fatal "--azure-vnet-cidr is required"
[[ -n "$p2s_cidr" ]] || fatal "--p2s-cidr is required"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Azure VNet CIDR" "$azure_vnet_cidr"
  print_kv "P2S CIDR" "$p2s_cidr"
  print_kv "K3s Pod CIDR" "$pod_cidr"
  print_kv "K3s Service CIDR" "$service_cidr"
  print_kv "Host Swap" "allowed"
  print_kv "Pod Swap" "$EDGE_KUBELET_SWAP_BEHAVIOR"
  print_kv "LAN CIDRs" "${lan_cidrs[*]:-auto-detect}"
  print_kv "Inventory" "$inventory"
  print_kv "Allow Battery" "$allow_battery"
  exit 0
fi

require_tools awk df install ip jq python3 ss systemctl uname
[[ "$(uname -s)" == "Linux" ]] || fatal "This setup path supports Ubuntu Linux only"
[[ -r /etc/os-release ]] || fatal "/etc/os-release is unavailable"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || fatal "Unsupported Linux distribution: ${ID:-unknown}"
[[ "${VERSION_ID:-}" == "22.04" || "${VERSION_ID:-}" == "24.04" ]] || \
  fatal "Supported Ubuntu releases are 22.04 and 24.04; found ${VERSION_ID:-unknown}"
[[ -f /sys/fs/cgroup/cgroup.controllers ]] || fatal "K3s swap support requires unified cgroup v2"
[[ "$EDGE_KUBELET_SWAP_BEHAVIOR" == "NoSwap" ]] || fatal "Supported Pod swap behavior is NoSwap"
cgroup_version="v2"

if [[ ${#lan_cidrs[@]} -eq 0 ]]; then
  default_interface=$(ip -4 route show default | awk 'NR == 1 {print $5}')
  [[ -n "$default_interface" ]] || fatal "No default-route interface detected; provide --lan-cidr"
  while IFS= read -r cidr; do
    [[ -n "$cidr" ]] && lan_cidrs+=("$cidr")
  done < <(ip -o -4 addr show dev "$default_interface" scope global | awk '{print $4}')
fi
[[ ${#lan_cidrs[@]} -gt 0 ]] || fatal "No LAN CIDR detected; provide --lan-cidr"

cidrs=("$azure_vnet_cidr" "$p2s_cidr" "$pod_cidr" "$service_cidr" "${lan_cidrs[@]}")
python3 - "${cidrs[@]}" <<'PYTHON'
import ipaddress
import sys

networks = []
for value in sys.argv[1:]:
    try:
        networks.append((value, ipaddress.ip_network(value, strict=False)))
    except ValueError as error:
        raise SystemExit(f"invalid CIDR {value!r}: {error}") from error

for index, (left_value, left) in enumerate(networks):
    for right_value, right in networks[index + 1 :]:
        if left.overlaps(right):
            raise SystemExit(f"CIDR overlap: {left_value} and {right_value}")
PYTHON

unknown_runtime=""
command -v kubeadm >/dev/null 2>&1 && unknown_runtime="kubeadm"
command -v microk8s >/dev/null 2>&1 && unknown_runtime="${unknown_runtime:+$unknown_runtime, }MicroK8s"
if command -v k3s >/dev/null 2>&1 && [[ ! -f /etc/rancher/k3s/.physical-ai-toolchain-managed ]]; then
  unknown_runtime="${unknown_runtime:+$unknown_runtime, }unmanaged K3s"
fi
if systemctl is-active --quiet containerd 2>/dev/null && [[ ! -f /etc/rancher/k3s/.physical-ai-toolchain-managed ]]; then
  unknown_runtime="${unknown_runtime:+$unknown_runtime, }unmanaged containerd"
fi
for unit in docker.service docker.socket podman.service podman.socket crio.service; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    unknown_runtime="${unknown_runtime:+$unknown_runtime, }active $unit"
  fi
done
if [[ -d /etc/cni/net.d ]] && find /etc/cni/net.d -mindepth 1 -maxdepth 1 -type f -print -quit | grep -q . && \
   [[ ! -f /etc/rancher/k3s/.physical-ai-toolchain-managed ]]; then
  unknown_runtime="${unknown_runtime:+$unknown_runtime, }unmanaged CNI"
fi
[[ -z "$unknown_runtime" ]] || fatal "Existing Kubernetes/runtime ownership is unknown: $unknown_runtime"

if [[ ! -f /etc/rancher/k3s/.physical-ai-toolchain-managed ]]; then
  for port in 6443 10250; do
    ss -H -lnt "sport = :$port" | grep -q . && fatal "Port $port is already in use by an unmanaged process"
  done
fi

swap_device_count=$(awk 'NR > 1 {count++} END {print count + 0}' /proc/swaps)
swap_active=false
(( swap_device_count > 0 )) && swap_active=true
swap_total_mib=$(awk 'NR > 1 {total += $3} END {printf "%d", total / 1024}' /proc/swaps)
swap_used_mib=$(awk 'NR > 1 {total += $4} END {printf "%d", total / 1024}' /proc/swaps)
swap_devices_json=$(awk 'NR > 1 {printf "%s\t%s\t%s\t%s\t%s\n", $1, $2, $3, $4, $5}' /proc/swaps | jq -R -s '
  split("\n") |
  map(select(length > 0) | split("\t") | {
    name: .[0],
    type: .[1],
    size_kib: (.[2] | tonumber),
    used_kib: (.[3] | tonumber),
    priority: (.[4] | tonumber)
  })
')
if (( swap_device_count > 0 )); then
  while read -r name type size used priority; do
    info "Active host swap: $name (type=$type, size=${size}KiB, used=${used}KiB, priority=$priority)"
  done < <(awk 'NR > 1 {print $1, $2, $3, $4, $5}' /proc/swaps)
fi

memory_mib=$(awk '/MemTotal/ {printf "%d", $2 / 1024}' /proc/meminfo)
(( memory_mib >= EDGE_MIN_MEMORY_MIB )) || fatal "Host memory ${memory_mib}MiB is below ${EDGE_MIN_MEMORY_MIB}MiB"
root_free_gib=$(df -Pk / | awk 'NR == 2 {printf "%d", $4 / 1024 / 1024}')
(( root_free_gib >= EDGE_MIN_DISK_GIB )) || fatal "Root filesystem has ${root_free_gib}GiB free; ${EDGE_MIN_DISK_GIB}GiB required"
root_free_inodes_percent=$(df -Pi / | awk 'NR == 2 {printf "%d", 100 - $5}')
(( root_free_inodes_percent >= 10 )) || fatal "Root filesystem has ${root_free_inodes_percent}% free inodes; 10% required"

time_status="unknown"
if command -v timedatectl >/dev/null 2>&1; then
  time_status=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)
  [[ "$time_status" == "yes" ]] || fatal "System time is not synchronized"
fi

power_source="ac-or-unknown"
if command -v on_ac_power >/dev/null 2>&1 && ! on_ac_power; then
  power_source="battery"
  [[ "$allow_battery" == "true" ]] || fatal "Host is using battery power; connect AC or pass --allow-battery"
fi

firewall_backend="none"
command -v ufw >/dev/null 2>&1 && firewall_backend="ufw"
command -v firewall-cmd >/dev/null 2>&1 && firewall_backend="firewalld"
command -v nft >/dev/null 2>&1 && firewall_backend="nftables"
gpu_present=false
[[ -e /dev/nvidia0 ]] && gpu_present=true

inventory_dir=$(dirname "$inventory")
tmp_inventory=$(mktemp)
jq -n \
  --arg generated_at "$(date -u +%FT%TZ)" \
  --arg ubuntu_version "$VERSION_ID" \
  --arg architecture "$(uname -m)" \
  --arg kernel "$(uname -r)" \
  --arg cgroup_version "$cgroup_version" \
  --arg kubelet_swap_behavior "$EDGE_KUBELET_SWAP_BEHAVIOR" \
  --argjson memory_mib "$memory_mib" \
  --argjson swap_active "$swap_active" \
  --argjson swap_total_mib "$swap_total_mib" \
  --argjson swap_used_mib "$swap_used_mib" \
  --argjson swap_devices "$swap_devices_json" \
  --argjson root_free_gib "$root_free_gib" \
  --argjson root_free_inodes_percent "$root_free_inodes_percent" \
  --arg time_synchronized "$time_status" \
  --arg power_source "$power_source" \
  --arg firewall_backend "$firewall_backend" \
  --arg azure_vnet_cidr "$azure_vnet_cidr" \
  --arg p2s_cidr "$p2s_cidr" \
  --arg pod_cidr "$pod_cidr" \
  --arg service_cidr "$service_cidr" \
  --argjson lan_cidrs "$(printf '%s\n' "${lan_cidrs[@]}" | jq -R . | jq -s .)" \
  --argjson gpu_present "$gpu_present" \
  '{schema_version: 2, generated_at: $generated_at, host: {ubuntu_version: $ubuntu_version, architecture: $architecture, kernel: $kernel, cgroup_version: $cgroup_version, memory_mib: $memory_mib, root_free_gib: $root_free_gib, root_free_inodes_percent: $root_free_inodes_percent, time_synchronized: $time_synchronized, power_source: $power_source, firewall_backend: $firewall_backend, gpu_present: $gpu_present, swap: {active: $swap_active, total_mib: $swap_total_mib, used_mib: $swap_used_mib, devices: $swap_devices, kubelet_behavior: $kubelet_swap_behavior}}, network: {lan_cidrs: $lan_cidrs, azure_vnet_cidr: $azure_vnet_cidr, p2s_cidr: $p2s_cidr, k3s_pod_cidr: $pod_cidr, k3s_service_cidr: $service_cidr}, checks: {cidrs_non_overlapping: true, runtime_ownership_known: true, swap_supported: true}}' \
  > "$tmp_inventory"
chmod 0600 "$tmp_inventory"
if [[ "$inventory" == /var/* ]]; then
  require_tools sudo
  sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" "$inventory_dir"
  install -m 0600 "$tmp_inventory" "$inventory"
else
  install -d -m 0700 "$inventory_dir"
  install -m 0600 "$tmp_inventory" "$inventory"
fi
rm -f "$tmp_inventory"

section "Deployment Summary"
print_kv "Ubuntu" "$VERSION_ID ($(uname -m))"
print_kv "Memory" "${memory_mib}MiB"
print_kv "Host Swap" "$([[ $swap_active == true ]] && echo "${swap_used_mib}MiB / ${swap_total_mib}MiB" || echo disabled)"
print_kv "Pod Swap" "$EDGE_KUBELET_SWAP_BEHAVIOR"
print_kv "Cgroup" "$cgroup_version"
print_kv "Root Free" "${root_free_gib}GiB"
print_kv "Free Inodes" "${root_free_inodes_percent}%"
print_kv "Time Sync" "$time_status"
print_kv "Power" "$power_source"
print_kv "Firewall" "$firewall_backend"
print_kv "GPU Present" "$gpu_present"
print_kv "Inventory" "$inventory"
info "Ubuntu edge preflight passed"
