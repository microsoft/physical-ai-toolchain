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

runtime_manifest="${TRAINING_DIR}/pyproject.toml"
runtime_requirements="$(mktemp)"
cleanup() {
  rm -f "${runtime_requirements}"
}
trap cleanup EXIT

if command -v uv &>/dev/null; then
  uv pip compile "${runtime_manifest}" -o "${runtime_requirements}"
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    uv pip install --no-cache-dir --requirement "${runtime_requirements}"
  else
    uv pip install --no-cache-dir --system --requirement "${runtime_requirements}"
  fi
else
  echo "Error: uv is required to compile workflow manifest dependencies" >&2
  exit 1
fi

exec "${python_cmd[@]}" -m training.scripts.policy_evaluation "$@"
