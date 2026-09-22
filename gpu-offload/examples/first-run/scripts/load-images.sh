#!/usr/bin/env bash
# Load the first-run offload images into the resolved cluster
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"

images="localhost/xavier-mutate:local localhost/gpu-offload-first-run:local"
if [ "$GPU_OFFLOAD_GPU_ENABLED" = "true" ]; then
  images="$images localhost/gpu-offload-first-run-gpu:local"
fi

# shellcheck disable=SC2086
exec scripts/load-images.sh $images
