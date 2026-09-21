#!/usr/bin/env bash
# Verify host and container runtime NVIDIA access
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "Platform $GPU_OFFLOAD_PLATFORM does not use a GPU; skipping"
  exit 0
fi

nvidia-smi
case "$GPU_OFFLOAD_PLATFORM" in
  wsl-nvidia)       test -c /dev/dxg ;;
  baremetal-nvidia) test -c /dev/nvidiactl ;;
esac

# On WSL, Podman is how the GPU reaches the kind node, so a failure here is
# fatal. On bare metal, k3s uses its own containerd and the NVIDIA runtime
# handler, so this only diagnoses the host toolkit and must not block setup.
if podman run --rm \
  --security-opt=label=disable \
  --device nvidia.com/gpu=all \
  docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04 \
  nvidia-smi; then
  echo "Podman GPU access verified"
elif [ "$GPU_OFFLOAD_RUNTIME" = "kind" ]; then
  echo "Podman cannot reach the GPU, which the kind path requires" >&2
  exit 1
else
  echo "WARNING: Podman cannot reach the GPU; k3s uses containerd directly, continuing" >&2
fi
