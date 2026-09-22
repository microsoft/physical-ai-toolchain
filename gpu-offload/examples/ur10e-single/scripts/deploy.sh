#!/usr/bin/env bash
# Deploy the ur10e-single control loop and its GPU server stage
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "The ur10e-single example needs a GPU node; platform $GPU_OFFLOAD_PLATFORM has none" >&2
  exit 1
fi

registry_host="${GPU_OFFLOAD_REGISTRY_HOST:-localhost:5000}"
namespace="${UR10E_NAMESPACE:-gpu-offload-ur10e}"
release="ur10e"

# The checkpoint and the tokenizer cache stay on the node; both paths are machine
# local, so they come from .env or from these defaults.
model_path="${UR10E_MODEL_HOST_PATH:-$HOME/pi05_ur10e_ik_20k/pretrained_model}"
cache_path="${UR10E_HF_CACHE_HOST_PATH:-$HOME/.cache/huggingface}"
test -f "$model_path/config.json" || {
  echo "No pi05 checkpoint at $model_path; set UR10E_MODEL_HOST_PATH in .env" >&2
  exit 1
}
test -f "$model_path/model.safetensors" || {
  echo "No model.safetensors at $model_path; join split weights with: cat $model_path/model.safetensors.part.* > $model_path/model.safetensors" >&2
  exit 1
}
test -d "$cache_path" || {
  echo "No HuggingFace cache at $cache_path; the gated PaliGemma tokenizer must be cached on the node" >&2
  exit 1
}

curl --silent --fail "http://$registry_host/v2/" > /dev/null || {
  echo "No registry on http://$registry_host/v2/; run registry/registry-up.sh first" >&2
  exit 1
}

helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" upgrade --install "$release" examples/ur10e-single \
  --namespace "$namespace" \
  --create-namespace \
  --set "image.registry=$registry_host" \
  --set "model.hostPath=$model_path" \
  --set "huggingFaceCache.hostPath=$cache_path" \
  --set "policy.mode=${UR10E_MODE:-self-check}" \
  --set "robot.usb.enabled=${UR10E_USB_ENABLED:-false}" \
  --set "robot.maxSteps=${UR10E_MAX_STEPS:-0}" \
  --set "robot.logEvery=${UR10E_LOG_EVERY:-50}" \
  --set "robot.debugInference=${UR10E_DEBUG_INFERENCE:-}"

server="$release-control-remote-server-gpu"
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait --for=create \
  "deployment/$server" \
  --namespace "$namespace" \
  --timeout=180s
# The server stage is generated from the client deployment, so the client has to
# exist first. Park it at zero replicas while the GPU pod settles: a client that
# calls load() against a server that is still being replaced leaves the call queued
# behind a connection to a pod that no longer exists.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" scale "deployment/$release-control" \
  --namespace "$namespace" \
  --replicas=0
# scale returns as soon as the replica count is recorded. Wait for the pods to be
# gone: a terminating client still calls load(), and that call reaches the incoming
# server and claims its single-instance slot for a connection that is about to
# close, which deadlocks the next client on a result that is never delivered.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait --for=delete pod \
  --namespace "$namespace" \
  --selector "app=$release-control" \
  --timeout=180s
# An exclusive GPU cannot be surged: the replacement pod stays Pending on
# "Insufficient nvidia.com/gpu" until the outgoing pod releases the device.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" patch "deployment/$server" \
  --namespace "$namespace" \
  --type=merge \
  --patch '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout restart "deployment/$server" \
  --namespace "$namespace"
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status "deployment/$server" \
  --namespace "$namespace" \
  --timeout=600s
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" scale "deployment/$release-control" \
  --namespace "$namespace" \
  --replicas=1
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status "deployment/$release-control" \
  --namespace "$namespace" \
  --timeout=300s
