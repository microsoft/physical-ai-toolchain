#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAINING_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC_DIR="$(cd "${TRAINING_DIR}/.." && pwd)"

ENV_FILE="${TRAINING_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

declare -a python_cmd
if [[ -n "${PYTHON:-}" ]]; then
  IFS=' ' read -r -a python_cmd <<< "${PYTHON}"
else
  python_cmd=(python)
fi

export PYTHONPATH="${SRC_DIR}:${PYTHONPATH:-}"

runtime_project="${SRC_DIR}/training/il/lerobot"

if [[ ! -f "${runtime_project}/uv.lock" ]]; then
  echo "Error: LeRobot lockfile not found at ${runtime_project}/uv.lock" >&2
  exit 1
fi

if command -v uv &>/dev/null; then
  # Export the fully-resolved set from the committed lock, then install with the
  # IL project as context so its override-dependencies and prerelease settings
  # apply during full resolution (the SIL path does not use --no-deps).
  runtime_requirements="$(mktemp)"
  trap 'rm -f "${runtime_requirements}"' EXIT
  uv export --frozen --no-hashes --no-emit-project --project "${runtime_project}" -o "${runtime_requirements}"
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    uv pip install --no-cache-dir --project "${runtime_project}" --requirement "${runtime_requirements}" || \
      uv pip install --no-cache-dir --project "${runtime_project}" --requirement "${runtime_requirements}" --index-strategy first-index \
        --extra-index-url https://download.pytorch.org/whl/cu124
  else
    uv pip install --no-cache-dir --system --project "${runtime_project}" --requirement "${runtime_requirements}" || \
      uv pip install --no-cache-dir --system --project "${runtime_project}" --requirement "${runtime_requirements}" --index-strategy first-index \
        --extra-index-url https://download.pytorch.org/whl/cu124
  fi
else
  echo "Error: uv is required to install workflow dependencies" >&2
  exit 1
fi

exec "${python_cmd[@]}" -m evaluation.sil.policy_evaluation "$@"
