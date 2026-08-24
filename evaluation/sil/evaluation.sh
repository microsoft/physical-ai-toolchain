#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ENV_FILE="${ROOT_DIR}/evaluation/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# Isaac Lab policy evaluation loads skrl/rsl-rl policies and imports
# first-party training packages (training.rl.simulation_shutdown and
# training.utils.integrity), so it runs on the same runtime as training.
# The job code snapshot stages training/rl alongside evaluation/sil (see
# submit-azureml-isaaclab-evaluation.sh).
ISAAC_PROJECT_DIR="${ROOT_DIR}/training/rl"
ISAAC_PYTHONPATH_ROOT="${ROOT_DIR}"

if [[ ! -f "${ISAAC_PROJECT_DIR}/uv.lock" ]]; then
  echo "Error: training/rl runtime not found at ${ISAAC_PROJECT_DIR} (expected in the job code snapshot)" >&2
  exit 1
fi

# shellcheck source=../../training/rl/scripts/setup_isaac_runtime.sh
source "${ISAAC_PROJECT_DIR}/scripts/setup_isaac_runtime.sh"

exec "${python_cmd[@]}" -m evaluation.sil.policy_evaluation "$@"
