#!/usr/bin/env bash
# Install the Microsoft GRID driver on Azure RTX PRO 6000 vGPU nodes.
set -o errexit -o nounset -o pipefail

readonly DRIVER_URL="https://download.microsoft.com/download/85beffdc-8361-4df4-a823-dcb1b230a7aa/NVIDIA-Linux-x86_64-580.105.08-grid-azure.run"
readonly DRIVER_SHA256="b360c7edf0686c7e47b1dc7980baa5c7740a00eb372cfafe045a28b4456fb32b"
readonly DRIVER_FILE="/tmp/NVIDIA-Linux-x86_64-580.105.08-grid-azure.run"
KERNEL_RELEASE="$(uname -r)"
readonly KERNEL_RELEASE

echo "=== Microsoft GRID Driver Installer for Azure RTX PRO 6000 ==="
echo "Kernel: ${KERNEL_RELEASE}"

if nvidia-smi 2>/dev/null; then
  echo "NVIDIA GRID driver already functional"
  nvidia-smi
  mkdir -p /run/nvidia/validations
  touch /run/nvidia/validations/.driver-ctr-ready
  # Expose host root at /run/nvidia/driver/ so the GPU Operator's
  # driver-validation init container finds binaries and libraries
  # at the same paths used by a containerized driver install.
  if ! mountpoint -q /run/nvidia/driver 2>/dev/null; then
    mount --bind / /run/nvidia/driver
  fi
  exit 0
fi

echo "Removing existing NVIDIA kernel modules..."
rmmod nvidia_uvm 2>/dev/null || true
rmmod nvidia_modeset 2>/dev/null || true
rmmod nvidia_drm 2>/dev/null || true
rmmod nvidia_peermem 2>/dev/null || true
rmmod nvidia 2>/dev/null || true

echo "Installing build dependencies..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  "linux-headers-${KERNEL_RELEASE}" build-essential wget 2>&1 | tail -5

echo "Downloading Microsoft GRID driver..."
wget -q -O "$DRIVER_FILE" "$DRIVER_URL"
echo "${DRIVER_SHA256}  ${DRIVER_FILE}" | sha256sum -c --quiet -
chmod +x "$DRIVER_FILE"

echo "Installing GRID driver (compiling kernel modules)..."
if ! "$DRIVER_FILE" --silent --no-drm 2>&1; then
  echo "Retrying with open kernel modules..."
  "$DRIVER_FILE" -M open --silent --no-drm 2>&1
fi

echo "Loading NVIDIA kernel modules..."
modprobe nvidia
modprobe nvidia-uvm || true
modprobe nvidia-modeset || true

nvidia-persistenced --persistence-mode || true

echo "=== Verification ==="
nvidia-smi

# Create the GPU Operator driver validation file.
# Other GPU Operator pods (toolkit, device-plugin, GFD, DCGM exporter,
# validator) have a driver-validation init container that polls for this
# file via a hostPath volume at /run/nvidia/validations/. Normally the
# GPU Operator's own driver DaemonSet creates it, but that pod is skipped
# on nodes labeled nvidia.com/gpu.deploy.driver=false.
echo "Creating GPU Operator driver validation marker..."
mkdir -p /run/nvidia/validations
touch /run/nvidia/validations/.driver-ctr-ready

# Expose host root at /run/nvidia/driver/ so the GPU Operator's
# driver-validation init container finds binaries and libraries
# at the same paths used by a containerized driver install.
if ! mountpoint -q /run/nvidia/driver 2>/dev/null; then
  mount --bind / /run/nvidia/driver
fi

echo "=== GRID driver installation complete ==="
