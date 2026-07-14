#!/usr/bin/env bash
# Install a pinned single-node K3s compute plane for edge OSMO workloads.
# cspell:ignore kubeconfigs microk crictl servicelb NOFILE readyz
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

Install and validate a pinned single-node K3s cluster without Arc or GPU dependencies.

OPTIONS:
    -h, --help               Show this help message
    --node-name NAME         K3s node name (default: current hostname)
    --context NAME           Kubeconfig context (default: $EDGE_K3S_CONTEXT)
    --pod-cidr CIDR          Pod CIDR (default: $EDGE_K3S_POD_CIDR)
    --service-cidr CIDR      Service CIDR (default: $EDGE_K3S_SERVICE_CIDR)
    --data-dir DIR           K3s data directory (default: $EDGE_K3S_DATA_DIR)
    --kubeconfig-out PATH    Protected operator kubeconfig output
    --skip-pvc-smoke         Skip local-path PVC smoke test
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --node-name hil-lab-01 \\
      --kubeconfig-out /var/lib/physical-ai-toolchain/kubeconfigs/hil-lab-01.yaml
EOF
}

version="$EDGE_K3S_VERSION"
node_name="${EDGE_NODE_NAME:-$(hostname -s 2>/dev/null || echo physical-ai-edge)}"
context="$EDGE_K3S_CONTEXT"
pod_cidr="$EDGE_K3S_POD_CIDR"
service_cidr="$EDGE_K3S_SERVICE_CIDR"
data_dir="$EDGE_K3S_DATA_DIR"
kubeconfig_out="${EDGE_KUBECONFIG:-$EDGE_STATE_DIR/kubeconfigs/$EDGE_K3S_CONTEXT.yaml}"
skip_pvc_smoke=false
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
    --skip-pvc-smoke)   skip_pvc_smoke=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Version" "$version"
  print_kv "Node Name" "$node_name"
  print_kv "Context" "$context"
  print_kv "Pod CIDR" "$pod_cidr"
  print_kv "Service CIDR" "$service_cidr"
  print_kv "Data Directory" "$data_dir"
  print_kv "Kubeconfig" "$kubeconfig_out"
  print_kv "PVC Smoke" "$([[ $skip_pvc_smoke == true ]] && echo skipped || echo enabled)"
  exit 0
fi

[[ "$node_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid Kubernetes node name: $node_name"
[[ "$(uname -s)" == "Linux" ]] || fatal "K3s installation supports Ubuntu Linux only"
require_tools curl install python3 sudo systemctl
architecture=$(uname -m)
case "$architecture" in
  x86_64)
    artifact="k3s"
    expected_sha="$EDGE_K3S_SHA256_AMD64"
    ;;
  aarch64|arm64)
    artifact="k3s-arm64"
    expected_sha="$EDGE_K3S_SHA256_ARM64"
    ;;
  *) fatal "Unsupported K3s architecture: $architecture" ;;
esac

if command -v kubeadm >/dev/null 2>&1 || command -v microk8s >/dev/null 2>&1; then
  fatal "Existing kubeadm or MicroK8s installation detected; refusing to mutate the host"
fi
if command -v k3s >/dev/null 2>&1 && [[ ! -f /etc/rancher/k3s/.physical-ai-toolchain-managed ]]; then
  fatal "Existing K3s installation is not owned by this setup path"
fi

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
managed_marker=/etc/rancher/k3s/.physical-ai-toolchain-managed

if [[ -f "$managed_marker" ]]; then
  current_version=$(/usr/local/bin/k3s --version | awk 'NR == 1 {print $3}')
  [[ "$current_version" == "$version" ]] || fatal "Managed K3s version is $current_version; expected $version"
  for expected in \
    "node-name: $node_name" \
    "data-dir: $data_dir" \
    "cluster-cidr: $pod_cidr" \
    "service-cidr: $service_cidr" \
    'write-kubeconfig-mode: "0600"' \
    'secrets-encryption: true'; do
    sudo grep -Fqx "$expected" /etc/rancher/k3s/config.yaml || \
      fatal "Managed K3s configuration drifted; missing: $expected"
  done
  info "Managed K3s $version configuration already matches; preserving additive settings"
else
  encoded_version=${version//+/%2B}
  binary_url="https://github.com/k3s-io/k3s/releases/download/${encoded_version}/${artifact}"
  curl -fsSL "$binary_url" -o "$tmp_dir/k3s"
  actual_sha=$(calculate_sha256 "$tmp_dir/k3s")
  [[ "$actual_sha" == "$expected_sha" ]] || fatal "K3s SHA-256 mismatch: expected $expected_sha, got $actual_sha"
  chmod 0755 "$tmp_dir/k3s"

  sudo install -d -m 0755 /etc/rancher/k3s "$data_dir"
  sudo install -m 0755 "$tmp_dir/k3s" /usr/local/bin/k3s
  for utility in kubectl crictl ctr; do
    if [[ ! -e "/usr/local/bin/$utility" ]]; then
      sudo ln -s /usr/local/bin/k3s "/usr/local/bin/$utility"
    fi
  done

  tmp_config="$tmp_dir/config.yaml"
  cat > "$tmp_config" <<EOF
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
  sudo install -m 0600 "$tmp_config" /etc/rancher/k3s/config.yaml
  sudo touch "$managed_marker"
  sudo chmod 0600 "$managed_marker"

tmp_unit="$tmp_dir/k3s.service"
cat > "$tmp_unit" <<'EOF'
[Unit]
Description=Lightweight Kubernetes
Documentation=https://docs.k3s.io
Wants=network-online.target
After=network-online.target

[Service]
Type=notify
Delegate=yes
KillMode=process
LimitNOFILE=1048576
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
  sudo install -m 0644 "$tmp_unit" /etc/systemd/system/k3s.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now k3s
fi

for utility in kubectl crictl ctr; do
  if [[ ! -e "/usr/local/bin/$utility" ]]; then
    sudo ln -s /usr/local/bin/k3s "/usr/local/bin/$utility"
  fi
done
sudo systemctl enable --now k3s

for ((attempt = 1; attempt <= 60; attempt++)); do
  sudo /usr/local/bin/k3s kubectl get --raw=/readyz >/dev/null 2>&1 && break
  (( attempt == 60 )) && fatal "K3s API did not become ready"
  sleep 2
done

sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" "$(dirname "$kubeconfig_out")"
sudo install -m 0600 /etc/rancher/k3s/k3s.yaml "$kubeconfig_out"
sudo chown "$(id -u):$(id -g)" "$kubeconfig_out"
kubectl --kubeconfig "$kubeconfig_out" config rename-context default "$context" >/dev/null 2>&1 || true
verify_kube_target "$kubeconfig_out" "$context" k3s
node_json=$(kube_kubectl "$kubeconfig_out" "$context" get nodes -o json)
jq -e --arg node "$node_name" --arg version "$version" '
  .items as $items |
  ($items | length) == 1 and
  $items[0].metadata.name == $node and
  $items[0].status.nodeInfo.kubeletVersion == $version
' <<< "$node_json" >/dev/null || fatal "K3s node identity or version does not match the requested configuration"

if [[ "$skip_pvc_smoke" == "false" ]]; then
  smoke_namespace="physical-ai-k3s-smoke"
  ensure_namespace "$kubeconfig_out" "$context" "$smoke_namespace"
  cat <<'EOF' | kube_kubectl "$kubeconfig_out" "$context" apply -f - >/dev/null
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: local-path-smoke
  namespace: physical-ai-k3s-smoke
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 16Mi
  storageClassName: local-path
---
apiVersion: v1
kind: Pod
metadata:
  name: local-path-smoke
  namespace: physical-ai-k3s-smoke
spec:
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 65534
    fsGroup: 65534
    seccompProfile:
      type: RuntimeDefault
  containers:
    - name: smoke
      image: alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1
      command: [sh, -c, "printf passed > /data/result && grep -qx passed /data/result"]
      securityContext:
        allowPrivilegeEscalation: false
        capabilities:
          drop: [ALL]
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: local-path-smoke
EOF
  kube_kubectl "$kubeconfig_out" "$context" wait -n "$smoke_namespace" \
    --for=jsonpath='{.status.phase}'=Succeeded pod/local-path-smoke --timeout=180s
  kube_kubectl "$kubeconfig_out" "$context" delete namespace "$smoke_namespace" --wait=true >/dev/null
fi

section "Deployment Summary"
print_kv "K3s Version" "$version"
print_kv "Node" "$node_name"
print_kv "Context" "$context"
print_kv "Pod CIDR" "$pod_cidr"
print_kv "Service CIDR" "$service_cidr"
print_kv "Kubeconfig" "$kubeconfig_out"
print_kv "Local Storage" "$([[ $skip_pvc_smoke == true ]] && echo 'not tested' || echo 'verified')"
info "K3s edge compute plane is ready"
