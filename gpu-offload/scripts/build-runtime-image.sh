#!/usr/bin/env bash
# Build the remoter runtime image that workload images layer the SDK from
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# The runtime Containerfile is FROM scratch and only adds the source tree, so the
# result is a payload image rather than a runnable one: workload builds copy the
# package out of it with COPY --from and install it against their own Python.
podman build \
  --file runtime/Containerfile \
  --tag localhost/pyremote:local \
  runtime
podman image exists localhost/pyremote:local
