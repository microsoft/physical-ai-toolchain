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
  podman info --format '{{.Host.Security.Rootless}} {{.Host.OCIRuntime.Name}}'
  # kind cannot delete its node when Podman is not allowed to stop pasta. Podman 4
  # keeps the namespace this probe creates, so only probe Podman 5 or later.
  podman_major="$(podman version --format '{{.Client.Version}}')"
  podman_major="${podman_major%%.*}"
  if [ -f /etc/apparmor.d/usr.bin.pasta ] && [ "$podman_major" -ge 5 ] &&
    ! podman unshare --rootless-netns true; then
    echo "Rootless Podman cannot tear down its network namespace, so kind would fail to delete the cluster." >&2
    echo "Run gpu-offload/scripts/allow-podman-pasta-signal.sh or mise run b-host-01-packages, then retry." >&2
    exit 1
  fi
fi
helm template gpu-offload helm/gpu-offload \
  --namespace gpu-offload \
  --set image.registry=localhost >/dev/null
echo "Preflight checks passed"
