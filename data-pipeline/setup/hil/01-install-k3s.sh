#!/usr/bin/env bash
# Install or upgrade one pinned, owned K3s compute plane without VPN or remote dependencies.
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

Install, verify, or upgrade one repository-owned single-node K3s compute plane.
VPN, Arc, GPU, storage integration, and remote OSMO administration are not required.

An installed K3s older than the pinned version is upgraded in place one minor
release at a time through EDGE_K3S_UPGRADE_STEPS, after a datastore backup.
Each release must bring kube-system workloads back before the next one starts.
A newer installed version is never downgraded.

OPTIONS:
    -h, --help               Show this help message
    --node-name NAME         Node name (default: existing node name, else current hostname)
    --context NAME           Kubeconfig context (default: $EDGE_K3S_CONTEXT)
    --pod-cidr CIDR          Pod CIDR (default: $EDGE_K3S_POD_CIDR)
    --service-cidr CIDR      Service CIDR (default: $EDGE_K3S_SERVICE_CIDR)
    --data-dir DIR           K3s data directory (default: $EDGE_K3S_DATA_DIR)
    --default-runtime NAME   Default container runtime (default: unset)
    --kubeconfig-out PATH    Protected operator kubeconfig output
    --allow-config-change    Allow rewriting the managed configuration of an existing node
    --config-preview         Print configuration and planned upgrade steps, then exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0") --node-name hil-lab-01
    $(basename "$0") --default-runtime nvidia
EOF
}

# Print "MAJOR MINOR PATCH BUILD" for vMAJOR.MINOR.PATCH+k3sBUILD; print nothing when unparseable.
k3s_version_fields() {
  if [[ "$1" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)\+k3s([0-9]+)$ ]]; then
    printf '%s %s %s %s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"
  fi
}

# Print -1, 0, or 1 when the first K3s version is older, equal, or newer than the second.
k3s_version_compare() {
  local -a left right
  local i
  read -r -a left <<< "$(k3s_version_fields "$1")"
  read -r -a right <<< "$(k3s_version_fields "$2")"
  for i in 0 1 2 3; do
    if (( left[i] < right[i] )); then echo -1; return 0; fi
    if (( left[i] > right[i] )); then echo 1; return 0; fi
  done
  echo 0
}

# Print the releases a node must install, oldest first, to move from the installed to the target version.
# Each intermediate minor needs exactly one pinned release because Kubernetes cannot skip minors.
k3s_upgrade_path() {
  local current="$1" target="$2" steps="$3"
  local -a cur tgt step
  local entry step_version found minor
  if [[ -z "$current" ]]; then
    echo "$target"
    return 0
  fi
  read -r -a cur <<< "$(k3s_version_fields "$current")"
  read -r -a tgt <<< "$(k3s_version_fields "$target")"
  (( ${#cur[@]} == 4 )) || fatal "Cannot parse installed K3s version: $current"
  (( ${#tgt[@]} == 4 )) || fatal "Cannot parse pinned K3s version: $target"
  (( cur[0] == tgt[0] )) || fatal "Installed K3s $current and pinned $target differ in major version"
  case "$(k3s_version_compare "$current" "$target")" in
    0) return 0 ;;
    1) fatal "Installed K3s $current is newer than pinned $target; refusing to downgrade" ;;
  esac
  for (( minor = cur[1] + 1; minor < tgt[1]; minor++ )); do
    found=""
    for entry in $steps; do
      step_version="${entry%%|*}"
      read -r -a step <<< "$(k3s_version_fields "$step_version")"
      if (( ${#step[@]} == 4 )) && (( step[0] == tgt[0] && step[1] == minor )); then
        [[ -z "$found" ]] || fatal "EDGE_K3S_UPGRADE_STEPS pins more than one release for v${tgt[0]}.${minor}"
        found="$step_version"
      fi
    done
    [[ -n "$found" ]] || fatal "No pinned K3s release for v${tgt[0]}.${minor}; add one to EDGE_K3S_UPGRADE_STEPS"
    echo "$found"
  done
  echo "$target"
}

# Print the pinned SHA-256 for one release and architecture column (2 = amd64, 3 = arm64).
k3s_release_sha() {
  local release="$1" column="$2" entry
  if [[ "$release" == "$version" ]]; then
    [[ "$column" == "2" ]] && echo "$EDGE_K3S_SHA256_AMD64" || echo "$EDGE_K3S_SHA256_ARM64"
    return 0
  fi
  for entry in $EDGE_K3S_UPGRADE_STEPS; do
    if [[ "${entry%%|*}" == "$release" ]]; then
      cut -d'|' -f"$column" <<< "$entry"
      return 0
    fi
  done
  fatal "No pinned SHA-256 for K3s $release"
}

# Print etcd, sqlite, or none for the datastore under the data directory. Some SQLite servers also keep
# an empty etcd/ directory, so only an initialized etcd member counts as etcd. Extra arguments go to sudo.
k3s_datastore_kind() {
  if sudo "$@" test -d "$data_dir/server/db/etcd/member"; then
    echo etcd
  elif sudo "$@" test -f "$data_dir/server/db/state.db"; then
    echo sqlite
  else
    echo none
  fi
}

# Describe the backup the install takes before the first binary change.
describe_datastore_backup() {
  case "$1" in
    etcd)   echo "etcd snapshot before the first release step" ;;
    sqlite) echo "SQLite, token, and certificate copy before the first release step (K3s stops briefly)" ;;
    *)      echo "no datastore found under $data_dir/server/db; the upgrade stops before any change" ;;
  esac
}

# Initialize the requested K3s version, node identity, network ranges, and kubeconfig destination.
version="$EDGE_K3S_VERSION"
k3s_binary="${EDGE_K3S_BINARY:-/usr/local/bin/k3s}"
existing_config=/etc/rancher/k3s/config.yaml
node_name="${EDGE_NODE_NAME:-}"
context="$EDGE_K3S_CONTEXT"
pod_cidr="$EDGE_K3S_POD_CIDR"
service_cidr="$EDGE_K3S_SERVICE_CIDR"
data_dir="$EDGE_K3S_DATA_DIR"
default_runtime="$EDGE_K3S_DEFAULT_RUNTIME"
kubeconfig_out="${HIL_KUBECONFIG:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml}"
allow_config_change=false
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
    --default-runtime)  default_runtime="$2"; shift 2 ;;
    --kubeconfig-out)   kubeconfig_out="$2"; shift 2 ;;
    --allow-config-change) allow_config_change=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

if [[ -n "$default_runtime" && ! "$default_runtime" =~ ^[A-Za-z0-9._-]+$ ]]; then
  fatal "--default-runtime must contain only letters, numbers, dots, underscores, and hyphens"
fi
[[ -n "$(k3s_version_fields "$version")" ]] || fatal "EDGE_K3S_VERSION is not a K3s release version: $version"
[[ "$EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] \
  || fatal "EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS must be a whole number of seconds: $EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS"
[[ "$EDGE_K3S_WORKLOAD_INTERVAL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] \
  || fatal "EDGE_K3S_WORKLOAD_INTERVAL_SECONDS must be a number of seconds: $EDGE_K3S_WORKLOAD_INTERVAL_SECONDS"

# Read the installed K3s version without privileges; an unreadable version stops before any change.
installed_version=""
if [[ -x "$k3s_binary" ]]; then
  installed_version=$("$k3s_binary" --version 2>/dev/null | awk 'NR == 1 {print $3}')
  [[ -n "$(k3s_version_fields "$installed_version")" ]] \
    || fatal "Cannot determine the installed K3s version from $k3s_binary"
fi
upgrade_output=$(k3s_upgrade_path "$installed_version" "$version" "$EDGE_K3S_UPGRADE_STEPS")
release_steps=()
[[ -z "$upgrade_output" ]] || mapfile -t release_steps <<< "$upgrade_output"

# Keep an existing node's registered name so a rerun or upgrade never registers a second node.
if [[ -z "$node_name" ]]; then
  if [[ "$config_preview" == "true" ]]; then
    node_name=$(sudo -n awk '$1 == "node-name:" {print $2; exit}' "$existing_config" 2>/dev/null || true)
    if [[ -z "$node_name" && -n "$installed_version" ]]; then
      node_name="existing node name (read with sudo during install)"
    fi
  elif sudo test -f "$existing_config"; then
    node_name=$(sudo awk '$1 == "node-name:" {print $2; exit}' "$existing_config")
  fi
  [[ -n "$node_name" ]] || node_name=$(hostname -s 2>/dev/null || echo physical-ai-edge)
fi

# Show the intended configuration and release steps, then exit without changing the host.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "host-ready"
  print_kv "Installed K3s" "${installed_version:-none}"
  print_kv "K3s Version" "$version"
  print_kv "Release Steps" "$( (( ${#release_steps[@]} )) && echo "${release_steps[*]}" || echo 'none (already at pinned version)')"
  if [[ -n "$installed_version" ]] && (( ${#release_steps[@]} )); then
    if sudo -n true 2>/dev/null; then
      print_kv "Datastore Backup" "$(describe_datastore_backup "$(k3s_datastore_kind -n)")"
    else
      print_kv "Datastore Backup" "detected with sudo during install"
    fi
  fi
  print_kv "Node" "$node_name"
  print_kv "Context" "$context"
  print_kv "Pod CIDR" "$pod_cidr"
  print_kv "Service CIDR" "$service_cidr"
  print_kv "Data Directory" "$data_dir"
  print_kv "Default Runtime" "${default_runtime:-K3s default}"
  print_kv "Kubeconfig" "$kubeconfig_out"
  print_kv "VPN" "not required"
  print_kv "Remote Access" "none"
  print_kv "Next" "02-connect-osmo-backend.sh after required environment inputs are available"
  exit 0
fi

# Check the commands used by the install path.
require_tools awk curl cut install jq sha256sum sudo systemctl
managed_marker=/etc/rancher/k3s/.physical-ai-toolchain-managed

architecture=$(uname -m)
case "$architecture" in
  x86_64)        artifact=k3s;       sha_column=2 ;;
  aarch64|arm64) artifact=k3s-arm64; sha_column=3 ;;
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
if [[ -n "$default_runtime" ]]; then
  printf 'default-runtime: %s\n' "$default_runtime" >> "$tmp_dir/config.yaml"
fi

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
ExecStart=$k3s_binary server --config /etc/rancher/k3s/config.yaml

[Install]
WantedBy=multi-user.target
EOF

# Wait until the K3s API answers, this node reports Ready, and kube-system workloads have rolled out.
wait_for_k3s() {
  local release="$1" attempt
  for ((attempt = 1; attempt <= 90; attempt++)); do
    if sudo "$k3s_binary" kubectl get --raw=/readyz >/dev/null 2>&1 \
      && sudo "$k3s_binary" kubectl wait --for=condition=Ready "node/$node_name" --timeout=5s >/dev/null 2>&1; then
      break
    fi
    (( attempt < 90 )) || fatal "K3s $release: API or node $node_name did not become ready; inspect 'journalctl -u k3s'. Pre-upgrade backups are under $data_dir/server/db"
    sleep 2
  done
  wait_for_kube_system_workloads "K3s $release" "$EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS" \
    "$EDGE_K3S_WORKLOAD_INTERVAL_SECONDS" sudo "$k3s_binary" kubectl \
    || fatal "Inspect 'sudo $k3s_binary kubectl get pods --namespace kube-system'; raise EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS for slow image pulls. Pre-upgrade backups are under $data_dir/server/db"
}

# Save the datastore once before the first binary change on an existing installation.
backup_datastore() {
  local stamp backup_dir
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  case "$(k3s_datastore_kind)" in
    etcd)
      sudo "$k3s_binary" etcd-snapshot save --data-dir "$data_dir" \
        --name "pre-upgrade-${installed_version//+/-}-$stamp" >/dev/null
      info "Saved an etcd snapshot under $data_dir/server/db/snapshots"
      ;;
    sqlite)
      backup_dir="$data_dir/server/db/upgrade-backups/$stamp-${installed_version//+/-}"
      # Stopping K3s keeps the SQLite copy consistent; workload containers keep running (KillMode=process).
      sudo systemctl stop k3s
      if ! sudo install -d -m 0700 "$backup_dir" \
        || ! sudo bash -c 'cp -a "$1"/server/db/state.db* "$1"/server/token "$1"/server/cred "$1"/server/tls "$2"/' \
          _ "$data_dir" "$backup_dir"; then
        sudo systemctl start k3s
        fatal "Could not copy the K3s datastore to $backup_dir; K3s restarted unchanged"
      fi
      info "Copied the SQLite datastore, token, and certificates to $backup_dir"
      ;;
    *)
      fatal "No K3s datastore found under $data_dir/server/db; refusing to upgrade without a backup"
      ;;
  esac
}

# Compare the managed configuration with the installed files. An existing node needs explicit consent for a
# change, checked before any download.
config_changed=false
if ! sudo cmp --silent "$tmp_dir/config.yaml" "$existing_config" 2>/dev/null; then
  config_changed=true
fi
if ! sudo cmp --silent "$tmp_dir/k3s.service" /etc/systemd/system/k3s.service 2>/dev/null; then
  config_changed=true
fi
if [[ "$config_changed" == "true" && -n "$installed_version" && "$allow_config_change" != "true" ]]; then
  sudo diff -u "$existing_config" "$tmp_dir/config.yaml" >&2 || true
  sudo diff -u /etc/systemd/system/k3s.service "$tmp_dir/k3s.service" >&2 || true
  fatal "The managed K3s configuration would change on an existing node; review the diff and rerun with --allow-config-change"
fi

# A matching version with a different binary digest is repaired in place.
target_sha=$(k3s_release_sha "$version" "$sha_column")
actual_sha=$(sudo sha256sum "$k3s_binary" 2>/dev/null | awk '{print $1}' || true)
if (( ${#release_steps[@]} == 0 )) && [[ "$actual_sha" != "$target_sha" ]]; then
  release_steps=("$version")
fi

# Download and verify every release before the backup stops K3s or any managed file changes, so a network
# or digest failure leaves the running node and its configuration untouched.
for release in "${release_steps[@]}"; do
  info "Downloading K3s $release"
  curl --fail --silent --show-error --location \
    "https://github.com/k3s-io/k3s/releases/download/${release//+/%2B}/${artifact}" --output "$tmp_dir/k3s-$release"
  printf '%s  %s\n' "$(k3s_release_sha "$release" "$sha_column")" "$tmp_dir/k3s-$release" | sha256sum -c -
done

# Back up an existing datastore before the first binary change; a failed backup leaves K3s running unchanged.
binary_changed=false
if (( ${#release_steps[@]} > 0 )) && [[ -n "$installed_version" ]]; then
  backup_datastore
fi

# Replace managed configuration only when its content changes.
sudo install -d -m 0755 /etc/rancher/k3s
sudo install -d -m 0700 "$data_dir"
if [[ "$config_changed" == "true" ]]; then
  sudo install -m 0600 "$tmp_dir/config.yaml" "$existing_config"
  sudo install -m 0644 "$tmp_dir/k3s.service" /etc/systemd/system/k3s.service
fi
sudo systemctl daemon-reload

# Install each verified release in order, restarting and waiting for readiness before the next minor.
for release in "${release_steps[@]}"; do
  info "Installing K3s $release"
  sudo install -m 0755 "$tmp_dir/k3s-$release" "$k3s_binary"
  binary_changed=true
  sudo systemctl enable k3s >/dev/null 2>&1
  sudo systemctl restart k3s
  wait_for_k3s "$release"
done
printf '%s\n' "physical-ai-toolchain:$version" > "$tmp_dir/managed"
sudo install -m 0600 "$tmp_dir/managed" "$managed_marker"

# Install the local kubectl wrapper when needed, then make sure K3s runs with the current configuration.
if [[ ! -e /usr/local/bin/kubectl ]]; then
  cat > "$tmp_dir/kubectl" <<EOF
#!/usr/bin/env bash
exec $k3s_binary kubectl "\$@"
EOF
  sudo install -m 0755 "$tmp_dir/kubectl" /usr/local/bin/kubectl
fi

sudo systemctl enable --now k3s
if [[ "$binary_changed" == "false" && "$config_changed" == "true" ]]; then
  sudo systemctl restart k3s
fi
wait_for_k3s "$version"

# Copy and rename the operator kubeconfig. The directory is created without sudo so any missing parents
# under the home directory stay owned by the operator.
install -d -m 0700 "$(dirname "$kubeconfig_out")"
sudo install -m 0600 -o "$(id -u)" -g "$(id -g)" /etc/rancher/k3s/k3s.yaml "$kubeconfig_out"
if kubectl --kubeconfig "$kubeconfig_out" config get-contexts default -o name 2>/dev/null | grep -qx default; then
  kubectl --kubeconfig "$kubeconfig_out" config rename-context default "$context" >/dev/null
fi
# Replace the local identity receipt after each successful run. An existing state directory keeps its
# owner and mode because other HiL tooling stores operator-readable files there.
identity_file="$EDGE_STATE_DIR/k3s-identity.json"
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
if ! sudo test -d "$EDGE_STATE_DIR"; then
  sudo install -d -m 0700 "$EDGE_STATE_DIR"
fi
sudo install -m 0600 "$identity_tmp" "$identity_file"

# Summarize the ready local compute plane and identify the next connection step.
section "Deployment Summary"
print_kv "Milestone" "host-ready local compute"
print_kv "K3s Version" "$version"
print_kv "Upgraded From" "$( [[ -n "$installed_version" && "$installed_version" != "$version" ]] && echo "$installed_version" || echo "not upgraded")"
print_kv "Node" "$node_name"
print_kv "Context" "$context"
print_kv "Kubeconfig" "$kubeconfig_out"
print_kv "Default Runtime" "${default_runtime:-K3s default}"
print_kv "Local Identity" "$identity_file"
print_kv "VPN Dependency" "none"
print_kv "Next" "Run $SCRIPT_DIR/02-connect-osmo-backend.sh after environment input preparation"
info "Local K3s compute plane is ready"
