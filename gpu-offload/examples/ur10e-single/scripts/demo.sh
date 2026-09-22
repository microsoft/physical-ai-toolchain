#!/usr/bin/env bash
# cspell:ignore fflush
# Run the ur10e-single Pi0.5 policy on the cluster and follow the run to completion
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"

namespace="${UR10E_NAMESPACE:-gpu-offload-ur10e}"
release="ur10e"

# A demo is a bounded run: the arm homes, performs the task once, and homes again.
# maxSteps of 0 would leave the loop cycling until the pod is deleted.
export UR10E_MODE="${UR10E_MODE:-headless}"
export UR10E_USB_ENABLED="${UR10E_USB_ENABLED:-true}"
export UR10E_MAX_STEPS="${UR10E_MAX_STEPS:-80}"
export UR10E_LOG_EVERY="${UR10E_LOG_EVERY:-7}"

echo "The UR10e moves to its home pose as soon as the policy loads, then runs the"
echo "policy for $UR10E_MAX_STEPS steps and homes again. Keep the workspace clear"
echo "and stay on the e-stop."
echo

examples/ur10e-single/scripts/deploy.sh

echo
echo "Following the run. Loading the checkpoint onto the GPU stage takes about 30s."
echo

# Stop at homed_on_exit rather than at headless_finished: the arm is still moving
# back to home after the last policy step.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs --follow "deployment/$release-control" \
  --namespace "$namespace" \
  | grep --line-buffered '"event"' \
  | awk '{ print; fflush() } /"homed_on_exit"/ { exit }'
