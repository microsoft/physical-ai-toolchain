#!/usr/bin/env bash
# Verify that the offloaded model ran on the GPU and the client has none
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "Platform $GPU_OFFLOAD_PLATFORM does not use a GPU; skipping"
  exit 0
fi

server="first-run-client-remote-server-${GPU_OFFLOAD_STAGE}"
result=""
i=0
while [ "$i" -lt 60 ]; do
  result="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs deployment/first-run-client \
    --namespace gpu-offload-demo 2>/dev/null | grep '"gpu_check"' | tail -n 1 || true)"
  [ -n "$result" ] && break
  sleep 5
  i=$((i + 1))
done
test -n "$result"

RESULT="$result" SERVER="$server" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["RESULT"])
check = report["gpu_check"]
server = os.environ["SERVER"]

print(json.dumps(report, indent=2, sort_keys=True))

assert "error" not in check, check["error"]
assert check["executed_by"].startswith(server + "-"), check["executed_by"]
assert check["cuda_available"] is True, "CUDA was not available on the server stage"
assert check["device_type"] == "cuda", check["device_type"]
assert check["logits_device"].startswith("cuda"), check["logits_device"]
assert check["cuda_device_count"] >= 1, check["cuda_device_count"]

# The GPU forward pass must agree with the identical seeded CPU model, proving
# the device produced correct results rather than uninitialised memory.
assert check["max_abs_diff_vs_cpu"] < 1e-3, check["max_abs_diff_vs_cpu"]
assert check["peak_memory_mib"] > 0, check["peak_memory_mib"]

# Negative control: the same code path reports a GPU only because it was
# offloaded. The client itself must see no GPU device at all.
assert report["client_gpu_devices"] == [], report["client_gpu_devices"]
assert report["client_host"] != check["executed_by"], "client and server are the same pod"

print()
print("GPU model check passed")
print("  executed on      :", check["executed_by"], "/", check["device_name"])
print("  compute capability:", check["compute_capability"])
print("  torch / cuda      :", check["torch_version"], "/", check["cuda_runtime_version"])
print("  forward / matmul  :", check["forward_ms"], "ms /", check["matmul_ms"], "ms")
print("  max diff vs cpu   :", check["max_abs_diff_vs_cpu"])
print("  client host       :", report["client_host"], "(no GPU devices)")
PY
