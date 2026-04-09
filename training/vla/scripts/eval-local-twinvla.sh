#!/usr/bin/env bash
# Evaluate a trained TwinVLA checkpoint in RoboTwin or Tabletop-Sim simulation
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

source "$REPO_ROOT/scripts/lib/common.sh"

# =============================================================================
# Help
# =============================================================================

show_help() {
  cat << 'EOF'
Usage: eval-local-twinvla.sh [OPTIONS]

Evaluate a trained TwinVLA checkpoint in RoboTwin or Tabletop-Sim simulation.
Reports success rate over N rollouts per task.

REQUIRED:
    -c, --checkpoint PATH        Path to checkpoint directory or HuggingFace model ID
    -t, --task NAME              Task name (e.g., open_laptop, aloha_handover_box)

EVALUATION OPTIONS:
        --simulator SIM          Simulator: robotwin, tabletop (default: auto-detect from task name)
        --task-config CONFIG     Task config: demo_clean, demo_randomized (default: demo_clean)
        --seed N                 Random seed (default: 42)
    -n, --num-rollouts N         Number of evaluation rollouts (default: 20)
    -g, --gpu-id N               GPU device ID (default: 0)
        --record-video           Record evaluation videos

WORKSPACE:
    -w, --workspace DIR          Workspace directory (default: ./workspace)
        --config-preview         Print configuration and exit
    -h, --help                   Show this help message

SIMULATORS:
    robotwin      RoboTwin 2.0 (SAPIEN-based, 50 bimanual tasks)
    tabletop      Tabletop-Sim (ALOHA-style bimanual tasks)

    Auto-detection: tasks starting with "aloha_" use tabletop, others use robotwin.

EXAMPLES:
    # Evaluate on RoboTwin open_laptop
    ./eval-local-twinvla.sh -c ./outputs/twinvla/checkpoint-10000 -t open_laptop

    # Evaluate with domain randomization
    ./eval-local-twinvla.sh -c ./outputs/twinvla/checkpoint-10000 -t open_laptop --task-config demo_randomized

    # Evaluate a HuggingFace checkpoint on Tabletop-Sim
    ./eval-local-twinvla.sh -c jellyho/TwinVLA-aloha_handover_box -t aloha_handover_box

    # Tabletop-Sim evaluation with video recording
    ./eval-local-twinvla.sh -c jellyho/aloha_dish_drainer -t aloha_dish_drainer --record-video
EOF
}

# =============================================================================
# Defaults
# =============================================================================

checkpoint=""
task_name=""
simulator=""
task_config="demo_clean"
seed=42
num_rollouts=20
gpu_id=0
record_video=false
workspace_dir="${SCRIPT_DIR}/workspace"
config_preview=false

# =============================================================================
# Argument Parsing
# =============================================================================

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    -c|--checkpoint)    checkpoint="$2"; shift 2 ;;
    -t|--task)          task_name="$2"; shift 2 ;;
    --simulator)        simulator="$2"; shift 2 ;;
    --task-config)      task_config="$2"; shift 2 ;;
    --seed)             seed="$2"; shift 2 ;;
    -n|--num-rollouts)  num_rollouts="$2"; shift 2 ;;
    -g|--gpu-id)        gpu_id="$2"; shift 2 ;;
    --record-video)     record_video=true; shift ;;
    -w|--workspace)     workspace_dir="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

[[ -z "$checkpoint" ]] && fatal "Checkpoint required. Use -c/--checkpoint"
[[ -z "$task_name" ]] && fatal "Task name required. Use -t/--task"

# Auto-detect simulator from task name prefix
if [[ -z "$simulator" ]]; then
  if [[ "$task_name" == aloha_* ]]; then
    simulator="tabletop"
  else
    simulator="robotwin"
  fi
fi

require_tools python

# =============================================================================
# Gather Configuration
# =============================================================================

robotwin_dir="${workspace_dir}/RoboTwin"
tabletop_dir="${workspace_dir}/Tabletop-Sim"
twinvla_dir="${workspace_dir}/TwinVLA"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Checkpoint" "$checkpoint"
  print_kv "Task" "$task_name"
  print_kv "Simulator" "$simulator"
  print_kv "Task config" "$task_config"
  print_kv "Seed" "$seed"
  print_kv "Rollouts" "$num_rollouts"
  print_kv "GPU" "$gpu_id"
  print_kv "Record video" "$record_video"
  exit 0
fi

# =============================================================================
# Validate Environment
# =============================================================================
section "Environment Validation"

case "$simulator" in
  robotwin)
    if [[ ! -d "$robotwin_dir" ]]; then
      fatal "RoboTwin not found at $robotwin_dir. Run setup-local-vla.sh first."
    fi
    if [[ ! -d "$robotwin_dir/policy/TwinVLA" ]]; then
      fatal "TwinVLA policy not linked in RoboTwin. Run setup-local-vla.sh first."
    fi
    ;;
  tabletop)
    if [[ ! -d "$tabletop_dir" ]]; then
      fatal "Tabletop-Sim not found at $tabletop_dir. Run setup-local-vla.sh first."
    fi
    ;;
  *)
    fatal "Unknown simulator: $simulator. Use 'robotwin' or 'tabletop'."
    ;;
esac

gpu_name=$(python -c "import torch; print(torch.cuda.get_device_name($gpu_id))" 2>/dev/null || echo "unknown")
info "GPU $gpu_id: $gpu_name"
info "Simulator: $simulator"
info "Task: $task_name ($task_config)"
info "Rollouts: $num_rollouts (seed: $seed)"

# =============================================================================
# Run Evaluation
# =============================================================================
section "Evaluation"

if [[ "$simulator" == "robotwin" ]]; then
  info "Running RoboTwin evaluation"
  cd "$robotwin_dir/policy/TwinVLA"

  eval_args=("$checkpoint" "$task_name" "$task_config" "$task_config" "$seed" "$gpu_id")
  info "Command: bash eval.sh ${eval_args[*]}"

  CUDA_VISIBLE_DEVICES="$gpu_id" bash eval.sh "${eval_args[@]}"

elif [[ "$simulator" == "tabletop" ]]; then
  info "Running Tabletop-Sim evaluation"
  cd "$twinvla_dir"

  info "Command: CUDA_VISIBLE_DEVICES=$gpu_id bash tabletop_run.sh $checkpoint $task_name"

  CUDA_VISIBLE_DEVICES="$gpu_id" bash tabletop_run.sh "$checkpoint" "$task_name"
fi

# =============================================================================
# Summary
# =============================================================================
section "Evaluation Summary"
print_kv "Checkpoint" "$checkpoint"
print_kv "Task" "$task_name"
print_kv "Simulator" "$simulator"
print_kv "Task config" "$task_config"
print_kv "GPU" "$gpu_name"
info "Check evaluation logs above for success rates"
