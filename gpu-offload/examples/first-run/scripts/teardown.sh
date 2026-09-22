#!/usr/bin/env bash
# Remove the offload workloads and demo state from the cluster
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"
helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" uninstall first-run \
  --namespace gpu-offload-demo --ignore-not-found
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete namespace gpu-offload-demo --ignore-not-found
helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" uninstall gpu-offload \
  --namespace gpu-offload --ignore-not-found
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete namespace gpu-offload --ignore-not-found

# The generated server Deployment holds the GPU, so confirm the device is free
# again before a follow-up run schedules a new stage.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait --for=delete \
  namespace/gpu-offload-demo --timeout=180s >/dev/null 2>&1 || true
echo "Offload workloads removed; the cluster is still running"
