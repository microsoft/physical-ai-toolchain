#!/usr/bin/env bash
# Build the admission controller image with Podman
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

secret_args=()
if [ -n "${PIP_INDEX_URL:-}" ]; then
  # passed as a BuildKit secret (read from this shell's env by podman itself),
  # never as a build arg, so a credential embedded in it never lands in image
  # history
  # shellcheck disable=SC2054  # one --secret value, not separate array elements
  secret_args=(--secret id=PIP_INDEX_URL,env=PIP_INDEX_URL)
fi

podman build "${secret_args[@]}" \
  --file controller/Containerfile \
  --tag localhost/xavier-mutate:local \
  controller
podman image exists localhost/xavier-mutate:local
