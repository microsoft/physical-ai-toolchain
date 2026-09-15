#!/usr/bin/env bash
# List the local clusters for the resolved runtime
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
  systemctl is-active k3s || echo "k3s is not running"
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get nodes -o wide 2>/dev/null || true
else
  KIND_EXPERIMENTAL_PROVIDER=podman kind get clusters
  podman ps --all --filter label=io.x-k8s.kind.cluster
fi
