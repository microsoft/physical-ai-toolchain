#!/usr/bin/env bash
# Load one or more local Podman images into the resolved cluster runtime
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
eval "$(scripts/detect-platform.sh --export)"

if [ "$#" -eq 0 ]; then
  echo "Usage: $(basename "$0") <image> [image...]" >&2
  exit 1
fi

for image in "$@"; do
  archive="$(mktemp --suffix=-gpu-offload-image.tar)"
  podman save --output "$archive" "$image"
  if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
    sudo k3s ctr images import "$archive"
  else
    KIND_EXPERIMENTAL_PROVIDER=podman kind load image-archive "$archive" \
      --name "$GPU_OFFLOAD_CLUSTER_NAME"
  fi
  rm -f "$archive"
  echo "Loaded $image"
done
