#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAINING_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${TRAINING_DIR}/../.." && pwd)"

ENV_FILE="${TRAINING_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

ISAAC_PROJECT_DIR="${TRAINING_DIR}"
ISAAC_PYTHONPATH_ROOT="${REPO_ROOT}"
# shellcheck source=setup_isaac_runtime.sh
source "${SCRIPT_DIR}/setup_isaac_runtime.sh"

backend="${TRAINING_BACKEND:-skrl}"
backend_lc=$(printf '%s' "$backend" | tr '[:upper:]' '[:lower:]')

case "${backend_lc}" in
  rsl-rl|rsl_rl|rslrl)
    exec "${python_cmd[@]}" -m training.rl.scripts.launch_rsl_rl "$@"
    ;;
  *)
    exec "${python_cmd[@]}" -m training.rl.scripts.launch "$@"
    ;;
esac
