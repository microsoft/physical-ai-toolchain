# shellcheck shell=bash

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

: "${ISAAC_PYTHONPATH_ROOT:?ISAAC_PYTHONPATH_ROOT must be set before sourcing isaac_python_prologue.sh}"

prebundle_path="/isaac-sim/exts/omni.pip.compute/pip_prebundle"
if [[ -d "${prebundle_path}" ]]; then
  export PYTHONPATH="${prebundle_path}:${ISAAC_PYTHONPATH_ROOT}:${PYTHONPATH:-}"
else
  export PYTHONPATH="${ISAAC_PYTHONPATH_ROOT}:${PYTHONPATH:-}"
fi
