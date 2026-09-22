#!/usr/bin/env bash
# Verify that the remoted function executed on the server stage
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU_OFFLOAD_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"
eval "$(scripts/detect-platform.sh --export)"
server="first-run-client-remote-server-${GPU_OFFLOAD_STAGE}"

result=""
i=0
while [ "$i" -lt 30 ]; do
  result="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs deployment/first-run-client \
    --namespace gpu-offload-demo 2>/dev/null | grep '"executed_by"' | grep -v '"gpu_check"' | tail -n 1 || true)"
  [ -n "$result" ] && break
  sleep 5
  i=$((i + 1))
done
test -n "$result"
printf '%s\n' "$result"

RESULT="$result" SERVER="$server" python3 - <<'PY'
import json
import os

result = json.loads(os.environ["RESULT"])
assert result["executed_by"].startswith(os.environ["SERVER"] + "-"), result["executed_by"]
assert result["predictions"] == [1, 4, 9, 16], result["predictions"]
print("Remote execution verified on", result["executed_by"])
PY
