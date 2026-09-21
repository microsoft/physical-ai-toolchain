#!/usr/bin/env bash
# Push the ur10e-single workload image to the host-local registry
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"

# The chart requests the image through the registry endpoint, so the cluster pulls
# it from the host rather than loading it into the node image store.
exec registry/registry-push.sh gpu-offload-ur10e-single:local
