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

runtime_requirements="${SRC_DIR}/training/il/lerobot/requirements.txt"

if [[ ! -f "${runtime_requirements}" ]]; then
  echo "Error: LeRobot requirements not found at ${runtime_requirements}" >&2
  exit 1
fi

if command -v uv &>/dev/null; then
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    uv pip install --no-cache-dir --requirement "${runtime_requirements}" || \
      uv pip install --no-cache-dir --requirement "${runtime_requirements}" --index-strategy first-index \
        --extra-index-url https://download.pytorch.org/whl/cu124
  else
    uv pip install --no-cache-dir --system --requirement "${runtime_requirements}" || \
      uv pip install --no-cache-dir --system --requirement "${runtime_requirements}" --index-strategy first-index \
        --extra-index-url https://download.pytorch.org/whl/cu124
  fi
else
  echo "Error: uv is required to install workflow dependencies" >&2
  exit 1
fi

exec "${python_cmd[@]}" -m evaluation.sil.policy_evaluation "$@"
