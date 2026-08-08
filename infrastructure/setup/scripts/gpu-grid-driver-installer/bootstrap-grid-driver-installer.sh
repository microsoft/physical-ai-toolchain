#!/usr/bin/env bash
# Copy the mounted installer script onto the host and run it via nsenter.
set -o errexit -o nounset -o pipefail

readonly CONTAINER_SCRIPT_DIR="/scripts"
readonly HOST_ROOT="/host"
readonly HOST_SCRIPT_DIR="${HOST_ROOT}/tmp/gpu-grid-driver-installer"
readonly INSTALL_SCRIPT_NAME="install-grid-driver.sh"
readonly CONTAINER_INSTALL_SCRIPT="${CONTAINER_SCRIPT_DIR}/${INSTALL_SCRIPT_NAME}"
readonly HOST_INSTALL_SCRIPT="${HOST_SCRIPT_DIR}/${INSTALL_SCRIPT_NAME}"
readonly NSENTER_INSTALL_SCRIPT="/tmp/gpu-grid-driver-installer/${INSTALL_SCRIPT_NAME}"

mkdir -p "$HOST_SCRIPT_DIR"
cp "$CONTAINER_INSTALL_SCRIPT" "$HOST_INSTALL_SCRIPT"
chmod +x "$HOST_INSTALL_SCRIPT"

echo "Running GRID driver installer on host via nsenter..."
nsenter --target 1 --mount --uts --ipc --net --pid -- /bin/bash "$NSENTER_INSTALL_SCRIPT"
