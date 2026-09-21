#!/usr/bin/env bash
# Build the ur10e-single workload image with the remoter SDK layered in
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GPU_OFFLOAD_DIR="$(cd "$EXAMPLE_DIR/../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"

# The ur10e-single deployment is a sibling workspace, not part of this repository.
ur10e_source="${UR10E_SOURCE_PATH:-$(cd "$GPU_OFFLOAD_DIR/../.." && pwd)/ur10e-single}"
test -f "$ur10e_source/pyproject.toml" || {
  echo "No ur10e-single deployment at $ur10e_source; set UR10E_SOURCE_PATH" >&2
  exit 1
}
test -f "$ur10e_source/uv.lock" || {
  echo "No uv.lock at $ur10e_source; the image is built from the committed lock" >&2
  exit 1
}

# Stage only the files the build needs. Copying them under the example keeps the
# build context inside gpu-offload instead of widening it to the parent directory,
# which would sweep in the sibling workspaces and their virtualenvs.
staging="$EXAMPLE_DIR/.ur10e-src"
rm -rf "$staging"
mkdir -p "$staging"
cp "$ur10e_source/pyproject.toml" "$ur10e_source/uv.lock" "$staging/"
cp -r "$ur10e_source/lerobot_robot_ur10e" "$ur10e_source/script" "$staging/"
find "$staging" -name '__pycache__' -type d -prune -exec rm -rf {} + 2> /dev/null || true
find "$staging" -name '*.egg-info' -type d -prune -exec rm -rf {} + 2> /dev/null || true

# The SDK ships as a payload image the workload build copies the package out of.
scripts/build-runtime-image.sh

build_args=(--build-arg "REMOTER_IMAGE=localhost/pyremote:local")
if [ -n "${UV_INDEX_URL:-}" ]; then
  build_args+=(--build-arg "UV_INDEX_URL=$UV_INDEX_URL")
fi

# Share the host uv cache with the build. The torch wheels alone are several
# gigabytes; without this every rebuild re-downloads the whole dependency set.
# Rootless Podman maps container root to the invoking user, so wheels fetched
# during the build land in the same cache a local `uv sync` uses.
cache_dir="${UV_CACHE_DIR:-$HOME/.cache/uv}"
mkdir -p "$cache_dir"

podman build "${build_args[@]}" \
  --volume "$cache_dir:/root/.cache/uv" \
  --file examples/ur10e-single/Containerfile \
  --tag localhost/gpu-offload-ur10e-single:local \
  .
podman image exists localhost/gpu-offload-ur10e-single:local
rm -rf "$staging"
