#!/usr/bin/env bash
# Delete the cluster for the resolved runtime
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
  if [ -x /usr/local/bin/k3s-uninstall.sh ]; then
    sudo /usr/local/bin/k3s-uninstall.sh
  else
    echo "k3s is not installed"
  fi
  kubectl config delete-context "$GPU_OFFLOAD_KUBE_CONTEXT" >/dev/null 2>&1 || true
  kubectl config delete-cluster "$GPU_OFFLOAD_KUBE_CONTEXT" >/dev/null 2>&1 || true
  kubectl config delete-user "$GPU_OFFLOAD_KUBE_CONTEXT" >/dev/null 2>&1 || true
else
  KIND_EXPERIMENTAL_PROVIDER=podman kind delete cluster --name "$GPU_OFFLOAD_CLUSTER_NAME"
fi
