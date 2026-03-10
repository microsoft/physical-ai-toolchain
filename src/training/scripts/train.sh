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

python_exec="/isaac-sim/kit/python/bin/python3"
if [[ ! -x "${python_exec}" ]]; then
  python_exec="${python_cmd[0]}"
fi

configure_uv() {
  local resolved_env
  if ! command -v uv &>/dev/null; then
    return 0
  fi
  if [[ -n "${python_exec}" ]]; then
    resolved_env="$("${python_cmd[@]}" -c 'import sys; print(sys.prefix)' 2>/dev/null || true)"
    export UV_PYTHON="${python_exec}"
    if [[ -n "${resolved_env}" && -d "${resolved_env}" ]]; then
      export UV_PROJECT_ENVIRONMENT="${resolved_env}"
      echo "uv configured with Python: ${python_exec}, environment: ${resolved_env}"
    else
      echo "uv configured with Python: ${python_exec}"
    fi
  else
    echo "Python executable not set; uv will use system discovery"
  fi
}

run_python() {
  if [[ -n "${python_exec}" ]]; then
    "${python_exec}" "$@"
  else
    "${python_cmd[@]}" "$@"
  fi
}

if ! command -v uv &>/dev/null; then
  echo "Installing uv package manager..."
  if curl -LsSf https://astral.sh/uv/0.10.9/install.sh | sh 2>/dev/null; then
    export PATH="${HOME}/.local/bin:${PATH}"
  fi
fi

configure_uv

prebundle_path="/isaac-sim/exts/omni.pip.compute/pip_prebundle"
if [[ -d "${prebundle_path}" ]]; then
  export PYTHONPATH="${prebundle_path}:${SRC_DIR}:${PYTHONPATH:-}"
else
  export PYTHONPATH="${SRC_DIR}:${PYTHONPATH:-}"
fi

if command -v uv &>/dev/null && [[ -n "${UV_PYTHON:-}" ]]; then
  uv pip uninstall -y scipy >/dev/null 2>&1 || true
  uv pip install --upgrade "numpy>=1.26.0,<2.0.0" || {
    echo "uv failed, falling back to pip..."
    run_python -m pip install --upgrade "numpy>=1.26.0,<2.0.0" --quiet
  }
else
  run_python -m pip uninstall -y scipy >/dev/null 2>&1 || true
  run_python -m pip install --upgrade "numpy>=1.26.0,<2.0.0" --quiet
fi

runtime_manifest="${TRAINING_DIR}/pyproject.toml"
runtime_requirements="$(mktemp)"
cleanup() {
  rm -f "${runtime_requirements}"
}
trap cleanup EXIT

if command -v uv &>/dev/null; then
  echo "uv detected, compiling and installing training manifest dependencies..."
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

backend="${TRAINING_BACKEND:-skrl}"
backend_lc=$(printf '%s' "$backend" | tr '[:upper:]' '[:lower:]')

case "${backend_lc}" in
  rsl-rl|rsl_rl|rslrl)
    exec "${python_cmd[@]}" -m training.scripts.launch_rsl_rl "$@"
    ;;
  *)
    exec "${python_cmd[@]}" -m training.scripts.launch "$@"
    ;;
esac
