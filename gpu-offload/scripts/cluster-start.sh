#!/usr/bin/env bash
# Start the stopped cluster for the resolved runtime
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
  sudo systemctl start k3s
else
  podman start "${GPU_OFFLOAD_CLUSTER_NAME}-control-plane"
  kubectl config use-context "$GPU_OFFLOAD_KUBE_CONTEXT"
fi
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait \
  --for=condition=Ready node --all --timeout=180s
