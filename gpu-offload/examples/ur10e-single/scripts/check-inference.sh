#!/usr/bin/env bash
# Verify the ur10e-single policy loaded on the GPU stage and returned actions
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"

namespace="${UR10E_NAMESPACE:-gpu-offload-ur10e}"
release="ur10e"
server="$release-control-remote-server-gpu"

# Reading roughly 7 GB of weights and warming CUDA takes a while on first start.
loaded=""
i=0
while [ "$i" -lt 120 ]; do
  loaded="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs "deployment/$release-control" \
    --namespace "$namespace" 2> /dev/null | grep '"event": "loaded"' | tail -n 1 || true)"
  [ -n "$loaded" ] && break
  sleep 10
  i=$((i + 1))
done
test -n "$loaded"

action=""
i=0
while [ "$i" -lt 30 ]; do
  action="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs "deployment/$release-control" \
    --namespace "$namespace" 2> /dev/null | grep '"event": "action"' | tail -n 1 || true)"
  [ -n "$action" ] && break
  sleep 5
  i=$((i + 1))
done
test -n "$action"

# The control pod must hold no GPU: the whole point is that the device is allocated
# to the generated server stage only.
client_gpu="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get "deployment/$release-control" \
  --namespace "$namespace" \
  -o jsonpath='{.spec.template.spec.containers[0].resources.limits.nvidia\.com/gpu}')"
server_gpu="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get "deployment/$server" \
  --namespace "$namespace" \
  -o jsonpath='{.spec.template.spec.containers[0].resources.limits.nvidia\.com/gpu}')"

LOADED="$loaded" ACTION="$action" SERVER="$server" \
  CLIENT_GPU="$client_gpu" SERVER_GPU="$server_gpu" python3 - << 'PY'
import json
import os

loaded = json.loads(os.environ["LOADED"])
action = json.loads(os.environ["ACTION"])
server = os.environ["SERVER"]
client_gpu = os.environ["CLIENT_GPU"]
server_gpu = os.environ["SERVER_GPU"]

print(json.dumps(loaded, indent=2, sort_keys=True))

assert loaded["executed_by"].startswith(server + "-"), loaded["executed_by"]
assert loaded["policy_class"] == "PI05Policy", loaded["policy_class"]
assert loaded["cuda_available"] is True, "CUDA was not available on the server stage"
assert loaded["device"] == "cuda", loaded["device"]
assert loaded["client_host"] != loaded["executed_by"], "client and server are the same pod"
assert action["action"], "the control loop received an empty action"
assert action["executed_by"] == loaded["executed_by"], "actions came from a different pod"
assert not client_gpu, f"the control container requested a GPU: {client_gpu}"
assert server_gpu == "1", f"the server stage did not request one GPU: {server_gpu!r}"

print()
print("ur10e-single offload verified")
print("  loaded on   :", loaded["executed_by"], "/", loaded["cuda_device_name"])
print("  policy      :", loaded["policy_class"])
print("  load time   :", loaded["load_seconds"], "s")
print("  client host :", action["client_host"], "(GPU limits: none)")
print("  action      :", action["action"])
print("  cycle       :", action["cycle_ms"], "ms")
PY
