#!/bin/bash
set -euo pipefail

PAYLOAD_ROOT="${PAYLOAD_ROOT:-/workspace/isaac_payload}"
TRAINING_ROOT="${TRAINING_ROOT:-${PAYLOAD_ROOT}/training/rl}"

mkdir -p "${PAYLOAD_ROOT}"
# Code is delivered via the OSMO url: input (object storage), not an
# env var: a single env string is capped at 128 KiB (MAX_ARG_STRLEN)
# and the archive exceeds that, failing the container execve E2BIG.
ARCHIVE_PATH="$(find "${OSMO_INPUT_0}" -maxdepth 2 -name '*.zip' | head -n1)"
if [[ -z "${ARCHIVE_PATH}" || ! -s "${ARCHIVE_PATH}" ]]; then
  echo "ERROR: no code archive found under the input mount; training payload is required." >&2
  ls -laR "${OSMO_INPUT_0}" || true
  exit 1
fi
unzip -oq "${ARCHIVE_PATH}" -d "${PAYLOAD_ROOT}"

if [[ -n "${SLEEP_AFTER_UNPACK:-}" ]]; then
  echo "SLEEP_AFTER_UNPACK set; sleeping for ${SLEEP_AFTER_UNPACK} after payload unpack"
  sleep "${SLEEP_AFTER_UNPACK}"
  echo "Finished post-unpack sleep; exiting"
  exit 0
fi

MODE="train"
if [[ "${RUN_AZURE_SMOKE_TEST:-0}" == "1" ]]; then
  MODE="smoke-test"
fi

train_args=(
  --mode "${MODE}"
  --task "${TASK:-}"
  --num_envs "${NUM_ENVS:-}"
  --checkpoint-uri "${CHECKPOINT_URI:-}"
  --checkpoint-mode "${CHECKPOINT_MODE:-from-scratch}"
  --register-checkpoint "${REGISTER_CHECKPOINT:-}"
  --headless
)
[[ -n "${MAX_ITERATIONS:-}" ]] && train_args+=(--max_iterations "${MAX_ITERATIONS}")

bash "${TRAINING_ROOT}/scripts/train.sh" "${train_args[@]}"
