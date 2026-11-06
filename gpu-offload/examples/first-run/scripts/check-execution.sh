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
  logs="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs deployment/first-run-client \
    --namespace gpu-offload-demo 2>&1 || true)"
  result="$(printf '%s\n' "$logs" | grep '"executed_by"' | grep -v '"gpu_check"' | tail -n 1 || true)"
  [ -n "$result" ] && break
  sleep 5
  i=$((i + 1))
done
if [ -z "$result" ]; then
  printf '%s\n' "No execution result found in first-run-client logs after 150 seconds." >&2
  printf '%s\n' "Inspect deployment/first-run-client in namespace gpu-offload-demo. Last logs:" "$logs" >&2
  exit 1
fi
printf '%s\n' "$result"

RESULT="$result" SERVER="$server" python3 - <<'PY'
import json
import os

line = os.environ["RESULT"]
try:
    # Runtime logging can surround the application's JSON with a prefix or suffix.
    result, _ = json.JSONDecoder().raw_decode(line[line.index("{"):])
except ValueError as exc:
    raise SystemExit(f"Malformed or truncated execution result: {exc}\nLog line: {line}") from exc

server = os.environ["SERVER"] + "-"
executed_by = result.get("executed_by")
if not isinstance(executed_by, str) or not executed_by.startswith(server):
    raise SystemExit(f"Expected executed_by to start with {server!r}, got {executed_by!r}")
if result.get("predictions") != [1, 4, 9, 16]:
    raise SystemExit(f"Expected predictions [1, 4, 9, 16], got {result.get('predictions')!r}")
print("Remote execution verified on", result["executed_by"])
PY
