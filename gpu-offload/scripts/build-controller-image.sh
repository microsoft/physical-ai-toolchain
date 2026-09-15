#!/usr/bin/env bash
# Build the admission controller image with Podman
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

build_args=""
if [ -n "${PIP_INDEX_URL:-}" ]; then
  build_args="--build-arg PIP_INDEX_URL=$PIP_INDEX_URL"
fi

# shellcheck disable=SC2086
podman build $build_args \
  --file controller/Containerfile \
  --tag localhost/xavier-mutate:local \
  controller
podman image exists localhost/xavier-mutate:local
