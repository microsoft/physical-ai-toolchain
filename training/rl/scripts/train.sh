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
  UV_VERSION="0.10.9"
  UV_SHA256="20d79708222611fa540b5c9ed84f352bcd3937740e51aacc0f8b15b271c57594"
  curl -LsSf "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/uv.tar.gz
  echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c --quiet -
  tar -xzf /tmp/uv.tar.gz -C /tmp
  mkdir -p "${HOME}/.local/bin"
  install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv "${HOME}/.local/bin/uv"
  install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uvx "${HOME}/.local/bin/uvx"
  rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu
  export PATH="${HOME}/.local/bin:${PATH}"
fi

configure_uv

prebundle_path="/isaac-sim/exts/omni.pip.compute/pip_prebundle"
if [[ -d "${prebundle_path}" ]]; then
  export PYTHONPATH="${prebundle_path}:${REPO_ROOT}:${PYTHONPATH:-}"
else
  export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
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

runtime_requirements="${TRAINING_DIR}/requirements.txt"

if command -v uv &>/dev/null; then
  echo "uv detected, installing training manifest dependencies..."
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    uv pip install --no-cache-dir --no-deps --requirement "${runtime_requirements}"
  else
    uv pip install --no-cache-dir --no-deps --system --requirement "${runtime_requirements}"
  fi
else
  echo "Error: uv is required to install workflow manifest dependencies" >&2
  exit 1
fi

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
