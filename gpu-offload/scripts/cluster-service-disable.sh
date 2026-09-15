#!/usr/bin/env bash
# Stop k3s from starting on boot, leaving it installed (bare-metal k3s only)
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_RUNTIME" != "k3s" ]; then
  echo "Runtime is $GPU_OFFLOAD_RUNTIME; boot control applies to k3s only"
  exit 0
fi
sudo systemctl disable k3s

boot="$(systemctl is-enabled k3s 2>/dev/null || true)"
state="$(systemctl is-active k3s 2>/dev/null || true)"
echo "Service:        k3s"
echo "Start on boot:  ${boot:-disabled}"
echo "Current state:  ${state:-inactive}"
