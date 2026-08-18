#!/bin/bash
set -euo pipefail

if [[ -z "${CHECKPOINT_URI:-}" ]]; then
  echo "checkpoint_uri is required" >&2
  exit 1
fi

PAYLOAD_ROOT="${PAYLOAD_ROOT:-/workspace/isaac_payload}"
INFERENCE_ROOT="${INFERENCE_ROOT:-${PAYLOAD_ROOT}/evaluation/sil}"

mkdir -p "${PAYLOAD_ROOT}"
# Code is delivered via the OSMO url: input (object storage), not an
# env var: a single env string is capped at 128 KiB (MAX_ARG_STRLEN)
# and the archive exceeds that, failing the container execve E2BIG.
ARCHIVE_PATH="$(find "${OSMO_INPUT_0}" -maxdepth 2 -name '*.zip' | head -n1)"
if [[ -z "${ARCHIVE_PATH}" || ! -s "${ARCHIVE_PATH}" ]]; then
  echo "ERROR: no code archive found under the input mount; runtime payload is required." >&2
  ls -laR "${OSMO_INPUT_0}" || true
  exit 1
fi
unzip -oq "${ARCHIVE_PATH}" -d "${PAYLOAD_ROOT}"

infer_args=(
  --task "${TASK:-}"
  --num-envs "${NUM_ENVS:-4}"
  --max-steps "${MAX_STEPS:-500}"
  --video-length "${VIDEO_LENGTH:-200}"
  --inference-format "${INFERENCE_FORMAT:-both}"
  --checkpoint-uri "${CHECKPOINT_URI:-}"
  --headless
)

bash "${INFERENCE_ROOT}/infer.sh" "${infer_args[@]}"
