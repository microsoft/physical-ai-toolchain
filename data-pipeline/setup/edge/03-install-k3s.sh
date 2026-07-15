#!/usr/bin/env bash
# Install a pinned single-node K3s compute plane for edge OSMO workloads.
# cspell:ignore configz dropin inotify kubeconfigs kubeletconfig microk nofile crictl servicelb readyz
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
  print_kv "Host Swap" "allowed"
  print_kv "Pod Swap" "$EDGE_KUBELET_SWAP_BEHAVIOR"
  print_kv "Inotify Instances" ">= $EDGE_INOTIFY_MAX_USER_INSTANCES"
  print_kv "Inotify Watches" ">= $EDGE_INOTIFY_MAX_USER_WATCHES"
  print_kv "System File Handles" ">= $EDGE_FILE_MAX"
  print_kv "Per-Process File Ceiling" ">= $EDGE_K3S_NOFILE_LIMIT"
  print_kv "K3s NOFILE" "$EDGE_K3S_NOFILE_LIMIT"
  print_kv "PVC Smoke" "$([[ $skip_pvc_smoke == true ]] && echo skipped || echo enabled)"
  exit 0
fi

[[ "$node_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid Kubernetes node name: $node_name"
[[ "$(uname -s)" == "Linux" ]] || fatal "K3s installation supports Ubuntu Linux only"
[[ -f /sys/fs/cgroup/cgroup.controllers ]] || fatal "K3s swap support requires unified cgroup v2"
[[ "$EDGE_KUBELET_SWAP_BEHAVIOR" == "NoSwap" ]] || fatal "Supported Pod swap behavior is NoSwap"
for value in \
  "$EDGE_INOTIFY_MAX_USER_INSTANCES" \
  "$EDGE_INOTIFY_MAX_USER_WATCHES" \
  "$EDGE_FILE_MAX" \
  "$EDGE_K3S_NOFILE_LIMIT"; do
  if [[ ! "$value" =~ ^[0-9]+$ ]] || (( value <= 0 )); then
    fatal "Edge sysctl floors must be positive integers"
  fi
done
require_tools awk cmp curl install jq python3 readlink sudo sysctl systemctl
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
fi

select_floor() {
  local current="${1:?current value required}" minimum="${2:?minimum value required}"
  if (( current < minimum )); then
    printf '%s\n' "$minimum"
  else
    printf '%s\n' "$current"
  fi
}

current_inotify_instances=$(sysctl -n fs.inotify.max_user_instances)
current_inotify_watches=$(sysctl -n fs.inotify.max_user_watches)
current_file_max=$(sysctl -n fs.file-max)
current_nr_open=$(sysctl -n fs.nr_open)
inotify_instances=$(select_floor "$current_inotify_instances" "$EDGE_INOTIFY_MAX_USER_INSTANCES")
inotify_watches=$(select_floor "$current_inotify_watches" "$EDGE_INOTIFY_MAX_USER_WATCHES")
file_max=$(select_floor "$current_file_max" "$EDGE_FILE_MAX")
nr_open=$(select_floor "$current_nr_open" "$EDGE_K3S_NOFILE_LIMIT")
sysctl_file=/etc/sysctl.d/90-physical-ai-k3s.conf
cat > "$tmp_dir/90-physical-ai-k3s.conf" <<EOF
fs.inotify.max_user_instances=$inotify_instances
fs.inotify.max_user_watches=$inotify_watches
fs.file-max=$file_max
fs.nr_open=$nr_open
EOF
if ! sudo cmp -s "$tmp_dir/90-physical-ai-k3s.conf" "$sysctl_file"; then
  sudo install -m 0644 "$tmp_dir/90-physical-ai-k3s.conf" "$sysctl_file"
fi
sudo sysctl --system >/dev/null
for setting in \
  "fs.inotify.max_user_instances:$EDGE_INOTIFY_MAX_USER_INSTANCES" \
  "fs.inotify.max_user_watches:$EDGE_INOTIFY_MAX_USER_WATCHES" \
  "fs.file-max:$EDGE_FILE_MAX" \
  "fs.nr_open:$EDGE_K3S_NOFILE_LIMIT"; do
  key=${setting%%:*}
  minimum=${setting#*:}
  effective=$(sysctl -n "$key")
  (( effective >= minimum )) || fatal "$key is $effective after reconciliation; expected at least $minimum"
done

k3s_was_active=false
systemctl is-active --quiet k3s 2>/dev/null && k3s_was_active=true
kubelet_config_changed=false
systemd_config_changed=false
kubelet_config_dir="$data_dir/agent/etc/kubelet.conf.d"
kubelet_swap_config="$kubelet_config_dir/10-physical-ai-swap.conf"
cat > "$tmp_dir/10-physical-ai-swap.conf" <<EOF
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
failSwapOn: false
memorySwap:
  swapBehavior: $EDGE_KUBELET_SWAP_BEHAVIOR
EOF
sudo install -d -m 0700 "$kubelet_config_dir"
if ! sudo cmp -s "$tmp_dir/10-physical-ai-swap.conf" "$kubelet_swap_config"; then
  sudo install -m 0600 "$tmp_dir/10-physical-ai-swap.conf" "$kubelet_swap_config"
  kubelet_config_changed=true
fi
systemd_dropin_dir=/etc/systemd/system/k3s.service.d
systemd_limit_config="$systemd_dropin_dir/10-physical-ai-limits.conf"
cat > "$tmp_dir/10-physical-ai-limits.conf" <<EOF
[Service]
LimitNOFILE=$EDGE_K3S_NOFILE_LIMIT
EOF
sudo install -d -m 0755 "$systemd_dropin_dir"
if ! sudo cmp -s "$tmp_dir/10-physical-ai-limits.conf" "$systemd_limit_config"; then
  sudo install -m 0644 "$tmp_dir/10-physical-ai-limits.conf" "$systemd_limit_config"
  systemd_config_changed=true
fi

if [[ -f "$managed_marker" ]]; then
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
  for utility in crictl ctr; do
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

tmp_unit="$tmp_dir/k3s.service"
cat > "$tmp_unit" <<EOF
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
  sudo install -m 0644 "$tmp_unit" /etc/systemd/system/k3s.service
  sudo systemctl daemon-reload
  sudo touch "$managed_marker"
  sudo chmod 0600 "$managed_marker"
fi

for utility in crictl ctr; do
  if [[ ! -e "/usr/local/bin/$utility" ]]; then
    sudo ln -s /usr/local/bin/k3s "/usr/local/bin/$utility"
  fi
done

kubectl_path=/usr/local/bin/kubectl
kubectl_target=""
should_install_kubectl_wrapper=false
if [[ ! -e "$kubectl_path" && ! -L "$kubectl_path" ]]; then
  should_install_kubectl_wrapper=true
elif [[ -L "$kubectl_path" ]]; then
  kubectl_target=$(readlink -f "$kubectl_path" 2>/dev/null || true)
  [[ "$kubectl_target" == /usr/local/bin/k3s ]] && should_install_kubectl_wrapper=true
fi
if [[ "$should_install_kubectl_wrapper" == "true" ]]; then
  cat > "$tmp_dir/kubectl" <<'EOF'
#!/usr/bin/env bash
export K3S_CONFIG_FILE=/dev/null
exec /usr/local/bin/k3s kubectl "$@"
EOF
  [[ -L "$kubectl_path" ]] && sudo rm -f "$kubectl_path"
  sudo install -m 0755 "$tmp_dir/kubectl" "$kubectl_path"
fi
command -v kubectl >/dev/null 2>&1 || fatal "kubectl is unavailable after K3s installation"

if [[ "$systemd_config_changed" == "true" ]]; then
  sudo systemctl daemon-reload
fi
sudo systemctl enable k3s >/dev/null
if [[ "$k3s_was_active" == "true" && \
  ( "$kubelet_config_changed" == "true" || "$systemd_config_changed" == "true" ) ]]; then
  sudo systemctl restart k3s
else
  sudo systemctl start k3s
fi

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
kubelet_config_json=$(kube_kubectl "$kubeconfig_out" "$context" \
  get --raw "/api/v1/nodes/${node_name}/proxy/configz")
jq -e --arg behavior "$EDGE_KUBELET_SWAP_BEHAVIOR" '
  .kubeletconfig.failSwapOn == false and
  .kubeletconfig.memorySwap.swapBehavior == $behavior
' <<< "$kubelet_config_json" >/dev/null || fatal "Effective kubelet swap configuration does not match the requested policy"
k3s_nofile=$(systemctl show k3s --property=LimitNOFILE --value)
[[ "$k3s_nofile" == "$EDGE_K3S_NOFILE_LIMIT" ]] || \
  fatal "K3s LimitNOFILE is $k3s_nofile; expected $EDGE_K3S_NOFILE_LIMIT"

if [[ "$skip_pvc_smoke" == "false" ]]; then
  smoke_namespace="physical-ai-k3s-smoke"
  ensure_namespace "$kubeconfig_out" "$context" "$smoke_namespace"
  kube_kubectl "$kubeconfig_out" "$context" delete pod local-path-smoke \
    -n "$smoke_namespace" --ignore-not-found --wait=true >/dev/null
  cat <<'EOF' | kube_kubectl "$kubeconfig_out" "$context" apply -f - >/dev/null
apiVersion: v1
kind: ServiceAccount
metadata:
  name: local-path-smoke
  namespace: physical-ai-k3s-smoke
automountServiceAccountToken: false
---
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
  serviceAccountName: local-path-smoke
  automountServiceAccountToken: false
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
print_kv "Host Swap" "$([[ -s /proc/swaps && $(awk 'END {print NR}' /proc/swaps) -gt 1 ]] && echo active || echo disabled)"
print_kv "Pod Swap" "$EDGE_KUBELET_SWAP_BEHAVIOR"
print_kv "Inotify Instances" "$(sysctl -n fs.inotify.max_user_instances)"
print_kv "Inotify Watches" "$(sysctl -n fs.inotify.max_user_watches)"
print_kv "System File Handles" "$(sysctl -n fs.file-max)"
print_kv "Per-Process File Ceiling" "$(sysctl -n fs.nr_open)"
print_kv "K3s NOFILE" "$k3s_nofile"
print_kv "Local Storage" "$([[ $skip_pvc_smoke == true ]] && echo 'not tested' || echo 'verified')"
info "K3s edge compute plane is ready"
