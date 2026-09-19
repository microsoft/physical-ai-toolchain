#!/usr/bin/env bash
# Drive the UR10e from the host with the ur10e-single Pi0.5 policy, no display
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GPU_OFFLOAD_DIR="$(cd "$EXAMPLE_DIR/../.." && pwd)"
cd "$GPU_OFFLOAD_DIR"

# shellcheck source=/dev/null
[ -f .env ] && . ./.env

# The ur10e-single deployment is a sibling workspace, not part of this repository.
source_dir="${UR10E_SOURCE_PATH:-$(cd "$GPU_OFFLOAD_DIR/../.." && pwd)/ur10e-single}"
checkpoint="${UR10E_CHECKPOINT_PATH:-${UR10E_MODEL_HOST_PATH:-}}"
robot_config="${UR10E_ROBOT_CONFIG:-$source_dir/script/ur10e_config_demo.json}"
task="${UR10E_TASK:-Pick up the large white gear and place it in the blue bin.}"
fps="${UR10E_FPS:-10}"
max_steps="${UR10E_MAX_STEPS:-0}"
home_speed="${UR10E_HOME_SPEED:-0.2}"
python_bin="${UR10E_PYTHON:-$source_dir/.venv/bin/python}"

if [ "${1:-}" = "--config-preview" ]; then
  printf '%-16s %s\n' \
    "source_dir" "$source_dir" \
    "checkpoint" "$checkpoint" \
    "robot_config" "$robot_config" \
    "task" "$task" \
    "fps" "$fps" \
    "max_steps" "$max_steps" \
    "home_speed" "$home_speed" \
    "python" "$python_bin"
  exit 0
fi

if [ -z "$checkpoint" ]; then
  echo "No checkpoint; set UR10E_CHECKPOINT_PATH or UR10E_MODEL_HOST_PATH in .env" >&2
  exit 1
fi

test -x "$python_bin"
test -f "$checkpoint/config.json"
test -f "$robot_config"

echo "The arm will move to the home pose before and after the run. Keep the workspace clear."

# The ur10e-single checkout shadows its own installed package when it is the
# working directory, so the loop runs from the example directory instead.
cd "$EXAMPLE_DIR"
exec env PYTHONPATH="$EXAMPLE_DIR" "$python_bin" run_ur10e.py \
  --mode headless \
  --checkpoint-path "$checkpoint" \
  --robot-config "$robot_config" \
  --task "$task" \
  --fps "$fps" \
  --max-steps "$max_steps" \
  --home-speed "$home_speed"
