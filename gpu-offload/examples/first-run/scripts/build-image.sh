#!/usr/bin/env bash
# Build the first-run client and GPU server-stage images with Podman
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"

# The SDK ships as a payload image the workload build copies the package out of.
scripts/build-runtime-image.sh

build_args=(--build-arg "REMOTER_IMAGE=localhost/pyremote:local")
if [ -n "${UV_INDEX_URL:-}" ]; then
  build_args+=(--build-arg "UV_INDEX_URL=$UV_INDEX_URL")
fi

# The Containerfile publishes two stages and the GPU one is last, so the target is
# always named rather than left to default.
podman build "${build_args[@]}" \
  --target client \
  --file examples/first-run/Containerfile \
  --tag localhost/gpu-offload-first-run:local \
  .
podman image exists localhost/gpu-offload-first-run:local

if [ "$GPU_OFFLOAD_GPU_ENABLED" = "true" ]; then
  # Share the host uv cache with the build. The torch wheels are several gigabytes
  # and would otherwise be re-downloaded on every rebuild. Rootless Podman maps
  # container root to the invoking user, so the wheels land in the same cache a
  # local `uv sync` uses.
  cache_dir="${UV_CACHE_DIR:-$HOME/.cache/uv}"
  mkdir -p "$cache_dir"

  podman build "${build_args[@]}" \
    --target gpu \
    --volume "$cache_dir:/root/.cache/uv" \
    --file examples/first-run/Containerfile \
    --tag localhost/gpu-offload-first-run-gpu:local \
    .
  podman image exists localhost/gpu-offload-first-run-gpu:local
fi
