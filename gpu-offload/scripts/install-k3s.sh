#!/usr/bin/env bash
# Install a single-node k3s cluster for the bare-metal GPU offload path
# cspell:ignore servicelb
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Pinned k3s release and the SHA-256 of the get.k3s.io installer that fetches it.
# Refresh both together: curl -fsSL https://get.k3s.io | sha256sum
K3S_VERSION="${K3S_VERSION:-v1.36.3+k3s1}"
K3S_INSTALL_SHA256="${K3S_INSTALL_SHA256:-ed01f89fd977bf20ac1516bbebf8370bf3ddbaa55dac8aba610956a4c78cc00b}"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Install a single-node k3s cluster and register its kubeconfig context.

k3s runs Kubernetes directly on the host, so a bare-metal NVIDIA GPU reaches
pods through the standard NVIDIA container stack instead of the nested device
passthrough that a containerised kind node would require.

OPTIONS:
    -h, --help               Show this help message
    -c, --context NAME       Kubeconfig context name (default: $DEFAULT_CONTEXT)
    -v, --version VERSION    k3s version to install (default: $K3S_VERSION)
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --version v1.36.3+k3s1
EOF
}

# Defaults
DEFAULT_CONTEXT="gpu-offload-k3s"
context="$DEFAULT_CONTEXT"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    -c|--context)      context="$2"; shift 2 ;;
    -v|--version)      K3S_VERSION="$2"; shift 2 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools curl sha256sum kubectl

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

kubeconfig="${KUBECONFIG:-$HOME/.kube/config}"
k3s_kubeconfig=/etc/rancher/k3s/k3s.yaml

installer=""
staged=""
merged=""
cleanup() { rm -f "$installer" "$staged" "$merged"; }
trap cleanup EXIT

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "k3s version" "$K3S_VERSION"
  print_kv "Kube context" "$context"
  print_kv "Kubeconfig" "$kubeconfig"
  exit 0
fi

#------------------------------------------------------------------------------
# Install k3s
#------------------------------------------------------------------------------
section "Installing k3s"

if command -v k3s > /dev/null 2>&1 && systemctl is-active --quiet k3s; then
  info "k3s is already installed and running: $(k3s --version | head -n 1)"
else
  installer="$(mktemp --suffix=-k3s-install.sh)"
  curl -fsSL https://get.k3s.io -o "$installer"
  echo "${K3S_INSTALL_SHA256}  ${installer}" | sha256sum -c - > /dev/null \
    || fatal "k3s installer checksum mismatch; refresh K3S_INSTALL_SHA256 in $(basename "$0")"
  info "Installer checksum verified"

  # Traefik and servicelb are unnecessary for the offload demo and only add
  # startup time; the kubeconfig mode lets the invoking user read it directly.
  INSTALL_K3S_VERSION="$K3S_VERSION" \
  INSTALL_K3S_EXEC="server --write-kubeconfig-mode 644 --disable traefik --disable servicelb" \
    sh "$installer"
fi

systemctl is-active --quiet k3s || fatal "k3s service is not active"

#------------------------------------------------------------------------------
# Register Kubeconfig Context
#------------------------------------------------------------------------------
section "Registering Kubeconfig Context"

staged="$(mktemp --suffix=-k3s-kubeconfig.yaml)"
merged="$(mktemp --suffix=-kubeconfig-merged.yaml)"

if [[ -r "$k3s_kubeconfig" ]]; then
  cat "$k3s_kubeconfig" > "$staged"
else
  # shellcheck disable=SC2024 # only the read needs root; $staged is user-owned
  sudo cat "$k3s_kubeconfig" > "$staged"
fi
# k3s names its cluster, user, and context "default". Renaming all three keeps
# the merged entries unique to this cluster, so the delete below can remove a
# previous install completely. Leaving the cluster named "default" makes a
# reinstall merge against a stale CA and fail TLS verification.
sed -i "s/: default\$/: ${context}/" "$staged"

mkdir -p "$(dirname "$kubeconfig")"
touch "$kubeconfig"
# Replace any previous entry so repeated installs stay idempotent.
kubectl --kubeconfig "$kubeconfig" config delete-context "$context" > /dev/null 2>&1 || true
kubectl --kubeconfig "$kubeconfig" config delete-cluster "$context" > /dev/null 2>&1 || true
kubectl --kubeconfig "$kubeconfig" config delete-user "$context" > /dev/null 2>&1 || true

KUBECONFIG="$kubeconfig:$staged" kubectl config view --flatten > "$merged"
install -m 600 "$merged" "$kubeconfig"

kubectl --context "$context" wait --for=condition=Ready node --all --timeout=180s

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "k3s version" "$(k3s --version | head -n 1)"
print_kv "Kube context" "$context"
print_kv "Kubeconfig" "$kubeconfig"
print_kv "Node" "$(kubectl --context "$context" get nodes -o jsonpath='{.items[0].metadata.name}')"
info "k3s cluster is ready"
