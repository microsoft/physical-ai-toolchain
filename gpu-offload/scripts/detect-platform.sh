#!/usr/bin/env bash
# Resolve the gpu-offload target platform, cluster runtime, and cluster identifiers
# cspell:ignore nvidiactl
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Resolve the target platform and cluster runtime for the gpu-offload tasks.

Auto-detection runs only when GPU_OFFLOAD_PLATFORM or GPU_OFFLOAD_RUNTIME is
unset or set to "auto". Define either variable in gpu-offload/.env to force a
path; mise loads that file for every task.

OPTIONS:
    -h, --help               Show this help message
    --export                 Print KEY=value lines for shell eval
    --config-preview         Print the resolved configuration and exit

EXAMPLES:
    $(basename "$0")
    eval "\$($(basename "$0") --export)"
EOF
}

# Defaults
export_mode=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --export)          export_mode=true; shift ;;
    --config-preview)  export_mode=false; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

# Detection probes are advisory; a missing device or tool means "not this platform".
is_wsl_nvidia() {
  [[ -c /dev/dxg && -d /usr/lib/wsl ]]
}

is_baremetal_nvidia() {
  [[ -c /dev/nvidiactl ]] && command -v nvidia-smi > /dev/null 2>&1
}

platform="${GPU_OFFLOAD_PLATFORM:-auto}"
if [[ "$platform" == "auto" ]]; then
  if is_wsl_nvidia; then
    platform="wsl-nvidia"
  elif is_baremetal_nvidia; then
    platform="baremetal-nvidia"
  else
    platform="cpu"
  fi
  platform_source="auto-detected"
else
  platform_source="forced by GPU_OFFLOAD_PLATFORM"
fi

case "$platform" in
  cpu|wsl-nvidia|baremetal-nvidia) ;;
  *) fatal "Invalid GPU_OFFLOAD_PLATFORM: $platform (expected cpu, wsl-nvidia, or baremetal-nvidia)" ;;
esac

runtime="${GPU_OFFLOAD_RUNTIME:-auto}"
if [[ "$runtime" == "auto" ]]; then
  # kind nests the node inside a container and cannot reach a bare-metal GPU
  # through rootless Podman, so bare metal defaults to a host-level k3s cluster.
  if [[ "$platform" == "baremetal-nvidia" ]]; then
    runtime="k3s"
  else
    runtime="kind"
  fi
  runtime_source="auto-detected"
else
  runtime_source="forced by GPU_OFFLOAD_RUNTIME"
fi

case "$runtime" in
  kind|k3s) ;;
  *) fatal "Invalid GPU_OFFLOAD_RUNTIME: $runtime (expected kind or k3s)" ;;
esac

if [[ "$platform" == "cpu" ]]; then
  gpu_enabled=false
  stage=cpu
else
  gpu_enabled=true
  stage=nvidia
fi

if [[ "$runtime" == "k3s" ]]; then
  cluster_name="gpu-offload-k3s"
  kube_context="gpu-offload-k3s"
elif [[ "$gpu_enabled" == "true" ]]; then
  cluster_name="gpu-offload-nvidia"
  kube_context="kind-gpu-offload-nvidia"
else
  cluster_name="gpu-offload"
  kube_context="kind-gpu-offload"
fi

if [[ "$export_mode" == "true" ]]; then
  cat << EOF
export GPU_OFFLOAD_PLATFORM='$platform'
export GPU_OFFLOAD_RUNTIME='$runtime'
export GPU_OFFLOAD_GPU_ENABLED='$gpu_enabled'
export GPU_OFFLOAD_STAGE='$stage'
export GPU_OFFLOAD_CLUSTER_NAME='$cluster_name'
export GPU_OFFLOAD_KUBE_CONTEXT='$kube_context'
EOF
  exit 0
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Resolved gpu-offload Configuration"
print_kv "Platform" "$platform ($platform_source)"
print_kv "Cluster runtime" "$runtime ($runtime_source)"
print_kv "GPU enabled" "$gpu_enabled"
print_kv "Server stage" "$stage"
print_kv "Cluster name" "$cluster_name"
print_kv "Kube context" "$kube_context"

if [[ ! -f "$SCRIPT_DIR/../.env" ]]; then
  info "No gpu-offload/.env found; copy .env.example to .env to override detection"
fi
