#!/usr/bin/env bash
# Remove the ur10e-single workloads and their host-path volumes
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"

namespace="${UR10E_NAMESPACE:-gpu-offload-ur10e}"
release="ur10e"

helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" uninstall "$release" \
  --namespace "$namespace" --ignore-not-found
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete namespace "$namespace" --ignore-not-found
# Retained PersistentVolumes outlive the release and would block a reinstall.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete pv "$release-model" "$release-hf-cache" --ignore-not-found
echo "ur10e-single workloads removed; the checkpoint on the node is untouched"
