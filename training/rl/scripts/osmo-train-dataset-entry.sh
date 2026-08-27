#!/bin/bash
set -euo pipefail

TRAINING_ROOT="${TRAINING_ROOT:-${OSMO_INPUT_0}/${OSMO_DATASET_NAME:-training-code}/training/rl}"

if [[ ! -d "${TRAINING_ROOT}" ]]; then
  echo "Error: Training root not found at ${TRAINING_ROOT}" >&2
  echo "Contents of ${OSMO_INPUT_0}:" >&2
  ls -la "${OSMO_INPUT_0}/" || true
  exit 1
fi

echo "Training folder mounted at: ${TRAINING_ROOT}"
ls -la "${TRAINING_ROOT}"

TRAINING_PACKAGE_ROOT="$(cd "${TRAINING_ROOT}/../.." && pwd)"
export PYTHONPATH="${TRAINING_PACKAGE_ROOT}:${PYTHONPATH:-}"

MODE="train"
if [[ "${RUN_AZURE_SMOKE_TEST:-0}" == "1" ]]; then
  MODE="smoke-test"
fi

train_args=(
  --mode "${MODE}"
  --task "${TASK:-}"
  --num_envs "${NUM_ENVS:-}"
  --max_iterations "${MAX_ITERATIONS:-}"
  --checkpoint-uri "${CHECKPOINT_URI:-}"
  --checkpoint-mode "${CHECKPOINT_MODE:-from-scratch}"
  --register-checkpoint "${REGISTER_CHECKPOINT:-}"
  --headless
)

bash "${TRAINING_ROOT}/scripts/train.sh" "${train_args[@]}"
