#!/usr/bin/env bash
# Deploy the first-run client and its server stage
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"

set -- --set image.registry=localhost --set "serverStage.name=$GPU_OFFLOAD_STAGE"
if [ "$GPU_OFFLOAD_GPU_ENABLED" = "true" ]; then
  # torch and a CUDA context need far more headroom than the CPU stage default.
  set -- "$@" \
    --set serverStage.gpu.enabled=true \
    --set "serverStage.gpu.platform=$GPU_OFFLOAD_PLATFORM" \
    --set serverStage.image.repository=gpu-offload-first-run-gpu \
    --set serverStage.resources.requests.cpu=500m \
    --set serverStage.resources.requests.memory=2Gi \
    --set serverStage.resources.limits.cpu=4 \
    --set serverStage.resources.limits.memory=8Gi
fi

helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" upgrade --install first-run examples/first-run \
  --namespace gpu-offload-demo \
  --create-namespace \
  "$@"

server="first-run-client-remote-server-${GPU_OFFLOAD_STAGE}"
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait --for=create \
  "deployment/$server" \
  --namespace gpu-offload-demo \
  --timeout=180s
# The local image tag is mutable, so rebuilding the server image does not change
# the pod spec and the running stage would keep serving the previous code.
# An exclusive GPU cannot be surged: the replacement pod stays Pending on
# "Insufficient nvidia.com/gpu" until the outgoing pod releases the device, so
# the GPU stage must replace rather than roll.
if [ "$GPU_OFFLOAD_GPU_ENABLED" = "true" ]; then
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" patch "deployment/$server" \
    --namespace gpu-offload-demo \
    --type=merge \
    --patch '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'
fi
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout restart "deployment/$server" \
  --namespace gpu-offload-demo
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status "deployment/$server" \
  --namespace gpu-offload-demo \
  --timeout=600s

# Remove a server stage left behind by a previous platform before restarting
# the client, so the client reconnects to the stage it is configured for.
for stale in cpu nvidia; do
  if [ "$stale" != "$GPU_OFFLOAD_STAGE" ]; then
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete deployment \
      "first-run-client-remote-server-${stale}" \
      --namespace gpu-offload-demo --ignore-not-found
  fi
done

kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout restart deployment/first-run-client \
  --namespace gpu-offload-demo
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status deployment/first-run-client \
  --namespace gpu-offload-demo \
  --timeout=300s
