#!/usr/bin/env bash
# Install the admission controller in the resolved cluster
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"

# build-controller-image.sh always tags the image localhost/xavier-mutate:local,
# and load-images.sh imports that exact archive/tag straight into the node's
# containerd for every runtime (k3s and kind alike) -- it is never pushed to the
# host-local registry under any other reference. Referencing it as
# $registry_host/xavier-mutate:local would ask the cluster to pull an image that
# was never published there, so this always uses the imported tag directly.
helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" upgrade --install gpu-offload helm/gpu-offload \
  --namespace gpu-offload \
  --create-namespace \
  --set image.registry=localhost \
  --set mutate.image.repository=xavier-mutate \
  --set mutate.image.tag=local \
  --set image.pullPolicy=Never
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status deployment/gpu-offload-mutate \
  --namespace gpu-offload \
  --timeout=180s
