#!/usr/bin/env bash
# Prepare this host for the local OSMO profile: a user-owned data root, K3s at the pinned release, and GPUs.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../data-pipeline/setup/defaults.conf
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

HIL_DIR="$REPO_ROOT/data-pipeline/setup/hil"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Run the one host step of the local OSMO profile. Run it as your user; it calls sudo itself.

  1. Creates the data root ($LOCAL_DATA_ROOT) with datasets/ and cache/ owned by you.
  2. Installs or upgrades K3s to $EDGE_K3S_VERSION one minor release at a time
     (data-pipeline/setup/hil/01-install-k3s.sh), after a datastore backup.
  3. Makes NVIDIA the default container runtime and installs the device plugin
     (data-pipeline/setup/hil/05-enable-k3s-gpu.sh).

K3s restarts during steps 2 and 3. Running workloads keep running, and Azure Arc
agents reconnect after each restart.

OPTIONS:
    -h, --help               Show this help message
    --skip-k3s               Skip the K3s install or upgrade
    --skip-gpu               Skip GPU enablement
    --allow-config-change    Pass --allow-config-change to the K3s installer
    --config-preview         Print the planned changes and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0")
EOF
}

skip_k3s=false
skip_gpu=false
installer_args=(--context "$LOCAL_KUBE_CONTEXT" --kubeconfig-out "$LOCAL_KUBECONFIG")
gpu_args=(--context "$LOCAL_KUBE_CONTEXT" --kubeconfig "$LOCAL_KUBECONFIG")
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --skip-k3s)             skip_k3s=true; shift ;;
    --skip-gpu)             skip_gpu=true; shift ;;
    --allow-config-change)  installer_args+=(--allow-config-change); shift ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

[[ "$EUID" -ne 0 ]] || fatal "Run this script as your user; it calls sudo for each privileged step"

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

data_root_state="missing (will be created)"
if [[ -d "$LOCAL_DATA_ROOT" ]]; then
  data_root_state="exists, owned by $(stat -c '%U' "$LOCAL_DATA_ROOT")"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Data Root" "$LOCAL_DATA_ROOT ($data_root_state)"
  print_kv "Datasets" "$LOCAL_DATASETS_DIR"
  print_kv "Caches" "$LOCAL_CACHE_DIR"
  print_kv "Owner" "$(id -un)"
  print_kv "Kubeconfig" "$LOCAL_KUBECONFIG"
  print_kv "Context" "$LOCAL_KUBE_CONTEXT"
  print_kv "K3s Step" "$([[ "$skip_k3s" == "true" ]] && echo skipped || echo "01-install-k3s.sh")"
  print_kv "GPU Step" "$([[ "$skip_gpu" == "true" ]] && echo skipped || echo "05-enable-k3s-gpu.sh")"
  if [[ "$skip_k3s" != "true" ]]; then
    "$HIL_DIR/01-install-k3s.sh" --config-preview "${installer_args[@]}"
  fi
  exit 0
fi

require_tools install stat sudo

#------------------------------------------------------------------------------
# Data Root
#------------------------------------------------------------------------------
section "Data Root"

for directory in "$LOCAL_DATA_ROOT" "$LOCAL_DATASETS_DIR" "$LOCAL_CACHE_DIR"; do
  if [[ -d "$directory" ]]; then
    info "Keeping existing $directory (owner $(stat -c '%U' "$directory"))"
  else
    sudo install -d -m 0755 -o "$(id -u)" -g "$(id -g)" "$directory"
    info "Created $directory"
  fi
done

#------------------------------------------------------------------------------
# K3s
#------------------------------------------------------------------------------

if [[ "$skip_k3s" != "true" ]]; then
  section "K3s"
  "$HIL_DIR/01-install-k3s.sh" "${installer_args[@]}"
fi

#------------------------------------------------------------------------------
# GPU
#------------------------------------------------------------------------------

if [[ "$skip_gpu" != "true" ]]; then
  section "GPU"
  "$HIL_DIR/05-enable-k3s-gpu.sh" "${gpu_args[@]}"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Data Root" "$LOCAL_DATA_ROOT"
print_kv "K3s Version" "$EDGE_K3S_VERSION"
print_kv "Next" "infrastructure/local/01-deploy-platform-services.sh"
info "Host preparation for the local OSMO profile is complete"
