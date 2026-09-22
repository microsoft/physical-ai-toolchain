#!/usr/bin/env bash
# Install NVIDIA Container Toolkit and generate the CDI specification
# cspell:ignore dearmor
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "Platform $GPU_OFFLOAD_PLATFORM does not use a GPU; skipping"
  exit 0
fi

# pinning-ignore: keyring for a GPG-signed apt repository, no stable digest
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor --yes \
  --output /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
# pinning-ignore: apt source list carrying only repository URLs
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update
sudo apt-get install --yes nvidia-container-toolkit
sudo mkdir -p /etc/cdi
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
