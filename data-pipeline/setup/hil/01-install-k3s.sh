#!/usr/bin/env bash
# Install one pinned, owned K3s compute plane without VPN or remote dependencies.
# cspell:ignore crio microk nofile readyz servicelb
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

Install or verify one repository-owned single-node K3s compute plane.
VPN, Arc, GPU, storage integration, and remote OSMO administration are not required.

OPTIONS:
    -h, --help               Show this help message
    --node-name NAME         Node name (default: current hostname)
    --context NAME           Kubeconfig context (default: $EDGE_K3S_CONTEXT)
    --pod-cidr CIDR          Pod CIDR (default: $EDGE_K3S_POD_CIDR)
    --service-cidr CIDR      Service CIDR (default: $EDGE_K3S_SERVICE_CIDR)
    --data-dir DIR           K3s data directory (default: $EDGE_K3S_DATA_DIR)
    --kubeconfig-out PATH    Protected operator kubeconfig output
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0") --node-name hil-lab-01
EOF
}

version="$EDGE_K3S_VERSION"
node_name="${EDGE_NODE_NAME:-$(hostname -s 2>/dev/null || echo physical-ai-edge)}"
context="$EDGE_K3S_CONTEXT"
pod_cidr="$EDGE_K3S_POD_CIDR"
service_cidr="$EDGE_K3S_SERVICE_CIDR"
data_dir="$EDGE_K3S_DATA_DIR"
kubeconfig_out="${HIL_KUBECONFIG:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml}"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --node-name)        node_name="$2"; shift 2 ;;
    --context)          context="$2"; shift 2 ;;
    --pod-cidr)         pod_cidr="$2"; shift 2 ;;
    --service-cidr)     service_cidr="$2"; shift 2 ;;
    --data-dir)         data_dir="$2"; shift 2 ;;
    --kubeconfig-out)   kubeconfig_out="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "host-ready"
  print_kv "K3s Version" "$version"
  print_kv "Node" "$node_name"
  print_kv "Context" "$context"
  print_kv "Pod CIDR" "$pod_cidr"
  print_kv "Service CIDR" "$service_cidr"
  print_kv "Data Directory" "$data_dir"
  print_kv "Kubeconfig" "$kubeconfig_out"
  print_kv "VPN" "not required"
  print_kv "Remote Access" "none"
  print_kv "Next" "02-connect-osmo-backend.sh after required environment inputs are available"
  exit 0
fi

operation="validate local K3s target"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: host-ready local compute"
  exit "$status"
}
trap report_failure ERR

[[ "$node_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid Kubernetes node name: $node_name"
[[ "$context" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal "Invalid Kubernetes context: $context"
[[ "$(uname -s)" == "Linux" ]] || fatal "K3s installation supports Ubuntu Linux only"
require_tools awk curl install ip jq python3 sha256sum ss sudo systemctl
python3 "$SCRIPT_DIR/check-network.py" "$pod_cidr" "$service_cidr"

managed_marker=/etc/rancher/k3s/.physical-ai-toolchain-managed
if command -v kubeadm >/dev/null 2>&1 || command -v microk8s >/dev/null 2>&1; then
  fatal "A foreign Kubernetes installation is present; refusing to mutate the host"
fi
if command -v k3s >/dev/null 2>&1 && ! sudo test -f "$managed_marker"; then
  fatal "Existing K3s installation is not owned by this setup path"
fi
if systemctl is-active --quiet kubelet 2>/dev/null && ! sudo test -f "$managed_marker"; then
  fatal "An unmanaged kubelet is active; refusing to mutate the host"
fi
for unit in containerd.service docker.service docker.socket podman.service podman.socket crio.service; do
  if systemctl is-active --quiet "$unit" 2>/dev/null && ! sudo test -f "$managed_marker"; then
    fatal "An unmanaged container runtime is active: $unit"
  fi
done
if [[ -d /etc/cni/net.d ]] && find /etc/cni/net.d -mindepth 1 -maxdepth 1 -type f -print -quit | grep -q . && \
   ! sudo test -f "$managed_marker"; then
  fatal "Unmanaged CNI configuration is present; refusing to mutate the host"
fi
if ! sudo test -f "$managed_marker"; then
  for port in 6443 10250; do
    ss -H -lnt "sport = :$port" | grep -q . && fatal "Port $port is already owned by another process"
  done
fi

for protected_path in "$managed_marker" /etc/rancher/k3s/config.yaml /etc/systemd/system/k3s.service \
  /usr/local/bin/k3s /usr/local/bin/kubectl "$kubeconfig_out"; do
  [[ ! -L "$protected_path" ]] || fatal "Managed K3s path must not be a symlink: $protected_path"
done
for directory in /usr/local/bin /etc/rancher /etc/rancher/k3s /etc/systemd/system "$(dirname "$kubeconfig_out")"; do
  require_no_symlink_path "$directory"
done
require_external_runtime_path "$kubeconfig_out"
require_no_symlink_path "$data_dir"
require_external_runtime_path "$data_dir"

architecture=$(uname -m)
case "$architecture" in
  x86_64)
    artifact=k3s
    expected_sha="$EDGE_K3S_SHA256_AMD64"
    ;;
  aarch64|arm64)
    artifact=k3s-arm64
    expected_sha="$EDGE_K3S_SHA256_ARM64"
    ;;
  *) fatal "Unsupported K3s architecture: $architecture" ;;
esac

tmp_dir=$(mktemp -d)
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT
chmod 0700 "$tmp_dir"

cat > "$tmp_dir/config.yaml" <<EOF
node-name: $node_name
data-dir: $data_dir
cluster-cidr: $pod_cidr
service-cidr: $service_cidr
write-kubeconfig-mode: "0600"
secrets-encryption: true
disable:
  - servicelb
  - traefik
EOF

cat > "$tmp_dir/k3s.service" <<EOF
[Unit]
Description=Lightweight Kubernetes
Documentation=https://docs.k3s.io
Wants=network-online.target
After=network-online.target

[Service]
Type=notify
Delegate=yes
KillMode=process
LimitNOFILE=$EDGE_K3S_NOFILE_LIMIT
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
TimeoutStartSec=0
Restart=always
RestartSec=5s
ExecStart=/usr/local/bin/k3s server --config /etc/rancher/k3s/config.yaml

[Install]
WantedBy=multi-user.target
EOF

if sudo test -f "$managed_marker"; then
  operation="verify owned K3s configuration"
  sudo cmp --silent "$tmp_dir/config.yaml" /etc/rancher/k3s/config.yaml || \
    fatal "Owned K3s configuration differs from the requested target"
  sudo cmp --silent "$tmp_dir/k3s.service" /etc/systemd/system/k3s.service || \
    fatal "Owned K3s service differs from the requested configuration"
  actual_sha=$(sudo sha256sum /usr/local/bin/k3s | awk '{print $1}')
  [[ "$actual_sha" == "$expected_sha" ]] || fatal "Owned K3s binary does not match the repository pin"
else
  for foreign_path in /etc/rancher/k3s/config.yaml /etc/systemd/system/k3s.service "$data_dir"; do
    sudo test ! -e "$foreign_path" || fatal "Existing unowned K3s path blocks setup: $foreign_path"
  done

  operation="download pinned K3s"
  encoded_version=${version//+/%2B}
  curl --fail --silent --show-error --location \
    "https://github.com/k3s-io/k3s/releases/download/${encoded_version}/${artifact}" --output "$tmp_dir/k3s"
  printf '%s  %s\n' "$expected_sha" "$tmp_dir/k3s" | sha256sum -c -

  operation="install owned K3s service"
  sudo install -d -m 0755 /etc/rancher/k3s
  sudo install -d -m 0700 "$data_dir"
  sudo install -m 0755 "$tmp_dir/k3s" /usr/local/bin/k3s
  sudo install -m 0600 "$tmp_dir/config.yaml" /etc/rancher/k3s/config.yaml
  sudo install -m 0644 "$tmp_dir/k3s.service" /etc/systemd/system/k3s.service
  printf '%s\n' "physical-ai-toolchain:$version" > "$tmp_dir/managed"
  sudo install -m 0600 "$tmp_dir/managed" "$managed_marker"
fi

if [[ ! -e /usr/local/bin/kubectl ]]; then
  cat > "$tmp_dir/kubectl" <<'EOF'
#!/usr/bin/env bash
exec /usr/local/bin/k3s kubectl "$@"
EOF
  sudo install -m 0755 "$tmp_dir/kubectl" /usr/local/bin/kubectl
fi

operation="start owned K3s service"
sudo systemctl daemon-reload
sudo systemctl enable --now k3s

operation="wait for K3s readiness"
for ((attempt = 1; attempt <= 60; attempt++)); do
  if sudo /usr/local/bin/k3s kubectl get --raw=/readyz >/dev/null 2>&1; then
    break
  fi
  (( attempt < 60 )) || fatal "K3s API did not become ready"
  sleep 2
done

operation="install protected operator kubeconfig"
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" "$(dirname "$kubeconfig_out")"
sudo install -m 0600 -o "$(id -u)" -g "$(id -g)" /etc/rancher/k3s/k3s.yaml "$kubeconfig_out"
if kubectl --kubeconfig "$kubeconfig_out" config get-contexts default -o name 2>/dev/null | grep -qx default; then
  kubectl --kubeconfig "$kubeconfig_out" config rename-context default "$context" >/dev/null
fi
verify_kube_target "$kubeconfig_out" "$context" k3s
node_json=$(kube_kubectl "$kubeconfig_out" "$context" get nodes -o json)
jq -e --arg node "$node_name" --arg version "$version" '
  (.items | length) == 1 and
  .items[0].metadata.name == $node and
  .items[0].status.nodeInfo.kubeletVersion == $version and
  any(.items[0].status.conditions[]; .type == "Ready" and .status == "True")
' <<< "$node_json" >/dev/null || fatal "K3s node identity, version, or readiness does not match"

operation="record root-owned local K3s identity"
identity_file=/var/lib/physical-ai-toolchain/k3s-identity.json
require_external_runtime_path "$identity_file"
require_no_symlink_path /var/lib/physical-ai-toolchain
sudo test ! -L "$identity_file" || fatal "Local K3s identity must not be a symlink"
identity_tmp="$tmp_dir/k3s-identity.json"
jq -n --arg kubeconfig "$kubeconfig_out" --arg context "$context" \
  --arg server "$(kube_api_server "$kubeconfig_out" "$context")" \
  --arg namespace_uid "$(kube_system_namespace_uid "$kubeconfig_out" "$context")" \
  --arg node "$node_name" --arg version "$version" '
  {schema_version: 1, kind: "physical-ai-local-k3s", kubeconfig: $kubeconfig,
   context: $context, api_server: $server, kube_system_namespace_uid: $namespace_uid,
   node_name: $node, k3s_version: $version}
' > "$identity_tmp"
chmod 0600 "$identity_tmp"
if sudo test -f "$identity_file"; then
  sudo cmp --silent "$identity_tmp" "$identity_file" || fatal "Root-owned local K3s identity has drifted"
else
  sudo install -d -m 0700 /var/lib/physical-ai-toolchain
  sudo install -m 0600 "$identity_tmp" "$identity_file"
fi

trap - ERR
section "Deployment Summary"
print_kv "Milestone" "host-ready local compute"
print_kv "K3s Version" "$version"
print_kv "Node" "$node_name"
print_kv "Context" "$context"
print_kv "Kubeconfig" "$kubeconfig_out"
print_kv "Local Identity" "$identity_file"
print_kv "VPN Dependency" "none"
print_kv "Next" "Run $SCRIPT_DIR/02-connect-osmo-backend.sh after environment input preparation"
info "Local K3s compute plane is ready"
