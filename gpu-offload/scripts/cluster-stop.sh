#!/usr/bin/env bash
# Stop the cluster without deleting it
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
  sudo systemctl stop k3s
else
  podman stop "${GPU_OFFLOAD_CLUSTER_NAME}-control-plane"
fi
