#!/usr/bin/env bash
# Check local tools and render the GPU offload Helm chart
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
kubectl version --client
helm version
# Podman builds the workload images on every platform. Its rootless and runtime
# configuration only matters for kind, where Podman also hosts the node.
podman --version
if [ "$GPU_OFFLOAD_RUNTIME" = "kind" ]; then
  kind version
  podman info --format '{{ "{{" }}.Host.Security.Rootless{{ "}}" }} {{ "{{" }}.Host.OCIRuntime.Name{{ "}}" }}'
fi
helm template gpu-offload helm/gpu-offload \
  --namespace gpu-offload \
  --set image.registry=localhost >/dev/null
echo "Preflight checks passed"
