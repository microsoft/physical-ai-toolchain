#!/usr/bin/env bash
# Install the Microsoft GRID driver on Azure RTX PRO 6000 vGPU nodes.
# Fallback for pools created with gpu_driver = "None"; AKS installs this driver
# itself on pools with gpu_driver = "Install".
set -o errexit -o nounset -o pipefail

# Microsoft's supported NCv6 GRID driver: vGPU 20.2 (R595)
readonly DRIVER_URL="https://download.microsoft.com/download/a7cb6d36-3bbc-43d6-9e88-e0842e6f9ab9/NVIDIA-Linux-x86_64-595.91.07-grid-azure.run"
readonly DRIVER_SHA256="72056e38ee17d86606ebdb97594d01cd1fe64b1fbd7e1a888634fa959529ca0a"
readonly DRIVER_FILE="/tmp/NVIDIA-Linux-x86_64-595.91.07-grid-azure.run"
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

# RTX PRO 6000 Blackwell GPUs require the open kernel modules.
echo "Installing GRID driver with open kernel modules..."
"$DRIVER_FILE" -M open --silent --no-drm 2>&1

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
