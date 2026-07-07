#!/usr/bin/env bash
# Shared helpers for LeRobot AzureML job entrypoints and pipeline components.
# Source this file; it defines functions only and must remain side-effect free.

# ensure_lerobot_runtime <venv_path> <lerobot_project_dir> <module>...
# Probe for the given Python modules; if any are missing, install the LeRobot
# runtime: apt packages (ffmpeg/git/build-essential) + a pinned uv, a Python
# 3.12 venv at <venv_path>, and the locked deps from <lerobot_project_dir>.
# Activates the venv in the caller's shell so later python calls resolve it.
ensure_lerobot_runtime() {
  local venv_path="$1"
  local lerobot_project="$2"
  shift 2

  if python3 - "$@" <<'PY'
import importlib.util
import sys

missing = []
for module in sys.argv[1:]:
    try:
        spec = importlib.util.find_spec(module)
    except ModuleNotFoundError:
        spec = None
    if spec is None:
        missing.append(module)
if missing:
    print("[lerobot-runtime] Missing runtime modules: " + ", ".join(missing))
    raise SystemExit(1)
PY
  then
    return
  fi

  if [[ ! -f "${lerobot_project}/uv.lock" ]]; then
    echo "ERROR: LeRobot lockfile not found at ${lerobot_project}/uv.lock" >&2
    exit 1
  fi

  apt-get update -qq && apt-get install -y -qq ffmpeg git build-essential >/dev/null 2>&1
  pip install --quiet --break-system-packages uv==0.7.12

  uv python install 3.12
  uv venv --python 3.12 "${venv_path}"
  # shellcheck disable=SC1091
  source "${venv_path}/bin/activate"
  uv export --frozen --no-hashes --no-emit-project --project "${lerobot_project}" \
    | uv pip install --no-cache-dir --no-deps -r -
}

# require_relative_dataset_repo_id
# Assert DATASET_REPO_ID is set and is a safe relative path (no leading '/',
# no '..'). Used by pipeline components when a prepared_dataset input is wired.
require_relative_dataset_repo_id() {
  : "${DATASET_REPO_ID:?DATASET_REPO_ID required when prepared_dataset is provided}"
  case "$DATASET_REPO_ID" in
    /*|*..*)
      echo "DATASET_REPO_ID must be relative (no absolute path, no ..): $DATASET_REPO_ID" >&2
      exit 1
      ;;
  esac
}
