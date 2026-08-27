#!/usr/bin/env bash
# Install one pinned, owned K3s compute plane without VPN or remote dependencies.
# cspell:ignore crio microk nofile readyz servicelb
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared helpers plus the pinned local K3s defaults.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

# Describe the local-only K3s installation inputs and the protected kubeconfig it produces.
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

# Initialize the requested K3s version, node identity, network ranges, and kubeconfig destination.
version="$EDGE_K3S_VERSION"
node_name="${EDGE_NODE_NAME:-$(hostname -s 2>/dev/null || echo physical-ai-edge)}"
context="$EDGE_K3S_CONTEXT"
pod_cidr="$EDGE_K3S_POD_CIDR"
service_cidr="$EDGE_K3S_SERVICE_CIDR"
data_dir="$EDGE_K3S_DATA_DIR"
kubeconfig_out="${HIL_KUBECONFIG:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml}"
config_preview=false

# Apply command-line overrides before validating the local host and K3s ownership boundary.
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

# Show the intended local compute configuration and exit without inspecting or changing the host.
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

# Check the commands used by the install path.
require_tools awk curl install jq sha256sum sudo systemctl
managed_marker=/etc/rancher/k3s/.physical-ai-toolchain-managed

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

# Build the requested K3s configuration, service unit, and temporary workspace with private permissions.
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

# Install the pinned binary when it is absent or differs from the requested version.
actual_sha=$(sudo sha256sum /usr/local/bin/k3s 2>/dev/null | awk '{print $1}' || true)
binary_changed=false
if [[ "$actual_sha" != "$expected_sha" ]]; then
  encoded_version=${version//+/%2B}
  curl --fail --silent --show-error --location \
    "https://github.com/k3s-io/k3s/releases/download/${encoded_version}/${artifact}" --output "$tmp_dir/k3s"
  printf '%s  %s\n' "$expected_sha" "$tmp_dir/k3s" | sha256sum -c -
  sudo install -m 0755 "$tmp_dir/k3s" /usr/local/bin/k3s
  binary_changed=true
fi

# Replace managed configuration and restart only when its content changes.
config_changed=false
if ! sudo cmp --silent "$tmp_dir/config.yaml" /etc/rancher/k3s/config.yaml 2>/dev/null; then
  config_changed=true
fi
if ! sudo cmp --silent "$tmp_dir/k3s.service" /etc/systemd/system/k3s.service 2>/dev/null; then
  config_changed=true
fi
sudo install -d -m 0755 /etc/rancher/k3s
sudo install -d -m 0700 "$data_dir"
if [[ "$config_changed" == "true" ]]; then
  sudo install -m 0600 "$tmp_dir/config.yaml" /etc/rancher/k3s/config.yaml
  sudo install -m 0644 "$tmp_dir/k3s.service" /etc/systemd/system/k3s.service
fi
printf '%s\n' "physical-ai-toolchain:$version" > "$tmp_dir/managed"
sudo install -m 0600 "$tmp_dir/managed" "$managed_marker"

# Install the local kubectl wrapper when needed, then start K3s and wait for its API to become ready.
if [[ ! -e /usr/local/bin/kubectl ]]; then
  cat > "$tmp_dir/kubectl" <<'EOF'
#!/usr/bin/env bash
exec /usr/local/bin/k3s kubectl "$@"
EOF
  sudo install -m 0755 "$tmp_dir/kubectl" /usr/local/bin/kubectl
fi

sudo systemctl daemon-reload
sudo systemctl enable --now k3s
if [[ "$binary_changed" == "true" || "$config_changed" == "true" ]]; then
  sudo systemctl restart k3s
fi

# Wait until the kubeconfig source is available.
for ((attempt = 1; attempt <= 60; attempt++)); do
  if sudo /usr/local/bin/k3s kubectl get --raw=/readyz >/dev/null 2>&1; then
    break
  fi
  (( attempt < 60 )) || fatal "K3s API did not become ready"
  sleep 2
done

# Copy and rename the operator kubeconfig.
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" "$(dirname "$kubeconfig_out")"
sudo install -m 0600 -o "$(id -u)" -g "$(id -g)" /etc/rancher/k3s/k3s.yaml "$kubeconfig_out"
if kubectl --kubeconfig "$kubeconfig_out" config get-contexts default -o name 2>/dev/null | grep -qx default; then
  kubectl --kubeconfig "$kubeconfig_out" config rename-context default "$context" >/dev/null
fi
# Replace the local identity receipt after each successful run.
identity_file=/var/lib/physical-ai-toolchain/k3s-identity.json
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
sudo install -d -m 0700 /var/lib/physical-ai-toolchain
sudo install -m 0600 "$identity_tmp" "$identity_file"

# Summarize the ready local compute plane and identify the next connection step.
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
