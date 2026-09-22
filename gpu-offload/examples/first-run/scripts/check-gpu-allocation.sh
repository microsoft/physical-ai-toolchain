#!/usr/bin/env bash
# Verify that only the server stage holds a GPU allocation
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "Platform $GPU_OFFLOAD_PLATFORM does not use a GPU; skipping"
  exit 0
fi

server="first-run-client-remote-server-${GPU_OFFLOAD_STAGE}"
server_gpu="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get "deployment/$server" \
  --namespace gpu-offload-demo \
  --output jsonpath='{.spec.template.spec.containers[0].resources.limits.nvidia\.com/gpu}')"
client_gpu="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get deployment/first-run-client \
  --namespace gpu-offload-demo \
  --output jsonpath='{.spec.template.spec.containers[0].resources.limits.nvidia\.com/gpu}')"

test "$server_gpu" = "1"
test -z "$client_gpu"
echo "Server GPU limit: $server_gpu"
echo "Client GPU limit: ${client_gpu:-none}"
