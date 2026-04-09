#!/usr/bin/env bash
# Train TwinVLA locally on a single GPU for bimanual manipulation
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

source "$REPO_ROOT/scripts/lib/common.sh"

# =============================================================================
# Help
# =============================================================================

show_help() {
  cat << 'EOF'
Usage: train-local-twinvla.sh [OPTIONS]

Train TwinVLA bimanual VLA on a local GPU. Designed for single-GPU
development on RTX 3090/4090/5090 (24-32 GB VRAM).

REQUIRED:
    -t, --task NAME              Task name (e.g., open_laptop, handover_box)

DATA OPTIONS:
    -d, --dataset-dir DIR        RLDS dataset directory (default: ./datasets/robotwin2_rlds)
        --dataset-format FORMAT  Dataset format: rlds, lerobot (default: rlds)
        --lerobot-repo ID        HuggingFace repo for LeRobot format datasets

TRAINING OPTIONS:
    -m, --model-type TYPE        VLM backbone: SmolVLM2VLA, Eagle2_1BVLA (default: SmolVLM2VLA)
    -b, --batch-size N           Batch size per GPU (default: 4)
    -s, --max-steps N            Maximum training steps (default: 50000)
        --save-steps N           Checkpoint save interval (default: 5000)
        --learning-rate LR       Learning rate (default: 2e-5)
    -o, --output-dir DIR         Output directory (default: ./outputs/twinvla)
    -g, --gpu-id N               GPU device ID (default: 0)
        --wandb-project NAME     Enable W&B logging to project

WORKSPACE:
    -w, --workspace DIR          TwinVLA installation directory (default: ./workspace/TwinVLA)
        --config-preview         Print configuration and exit
    -h, --help                   Show this help message

EXAMPLES:
    # Quick test on open_laptop (SmolVLM2, batch 4, 5K steps)
    ./train-local-twinvla.sh -t open_laptop -s 5000

    # Full training on handover_box with W&B
    ./train-local-twinvla.sh -t handover_box -s 50000 --wandb-project twinvla-experiments

    # LeRobot format from HuggingFace
    ./train-local-twinvla.sh -t aloha_handover_box --dataset-format lerobot --lerobot-repo jellyho/aloha_handover_box
EOF
}

# =============================================================================
# Defaults
# =============================================================================

task_name=""
dataset_dir="${SCRIPT_DIR}/datasets/robotwin2_rlds"
dataset_format="rlds"
lerobot_repo=""
model_type="SmolVLM2VLA"
batch_size=4
max_steps=50000
save_steps=5000
learning_rate="2e-5"
output_dir="${SCRIPT_DIR}/outputs/twinvla"
gpu_id=0
wandb_project=""
workspace_dir="${SCRIPT_DIR}/workspace/TwinVLA"
config_preview=false

# =============================================================================
# Argument Parsing
# =============================================================================

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -t|--task)            task_name="$2"; shift 2 ;;
    -d|--dataset-dir)     dataset_dir="$2"; shift 2 ;;
    --dataset-format)     dataset_format="$2"; shift 2 ;;
    --lerobot-repo)       lerobot_repo="$2"; shift 2 ;;
    -m|--model-type)      model_type="$2"; shift 2 ;;
    -b|--batch-size)      batch_size="$2"; shift 2 ;;
    -s|--max-steps)       max_steps="$2"; shift 2 ;;
    --save-steps)         save_steps="$2"; shift 2 ;;
    --learning-rate)      learning_rate="$2"; shift 2 ;;
    -o|--output-dir)      output_dir="$2"; shift 2 ;;
    -g|--gpu-id)          gpu_id="$2"; shift 2 ;;
    --wandb-project)      wandb_project="$2"; shift 2 ;;
    -w|--workspace)       workspace_dir="$2"; shift 2 ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

[[ -z "$task_name" ]] && fatal "Task name required. Use -t/--task (e.g., -t open_laptop)"

require_tools python git

# =============================================================================
# Gather Configuration
# =============================================================================

if [[ "$dataset_format" == "lerobot" ]]; then
  data_source="${lerobot_repo:-$task_name}"
else
  data_source="$dataset_dir"
fi

# Estimate VRAM requirement
case "$model_type" in
  SmolVLM2VLA)  vram_estimate="~16 GB" ;;
  Eagle2_1BVLA) vram_estimate="~24 GB" ;;
  *)            vram_estimate="unknown" ;;
esac

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Task" "$task_name"
  print_kv "Dataset format" "$dataset_format"
  print_kv "Data source" "$data_source"
  print_kv "Model type" "$model_type"
  print_kv "VRAM estimate" "$vram_estimate"
  print_kv "Batch size" "$batch_size"
  print_kv "Max steps" "$max_steps"
  print_kv "Save steps" "$save_steps"
  print_kv "Learning rate" "$learning_rate"
  print_kv "Output directory" "$output_dir"
  print_kv "GPU" "$gpu_id"
  print_kv "W&B project" "${wandb_project:-disabled}"
  print_kv "TwinVLA directory" "$workspace_dir"
  exit 0
fi

# =============================================================================
# Validate Environment
# =============================================================================
section "Environment Validation"

if [[ ! -d "$workspace_dir" || ! -f "$workspace_dir/setup.py" ]]; then
  fatal "TwinVLA not found at $workspace_dir. Run setup-local-vla.sh first."
fi

if [[ "$dataset_format" == "rlds" && ! -d "$dataset_dir" ]]; then
  fatal "RLDS dataset not found at $dataset_dir. Run setup-local-vla.sh first."
fi

gpu_count=$(python -c "import torch; print(torch.cuda.device_count())" 2>/dev/null || echo "0")
if [[ "$gpu_count" == "0" ]]; then
  fatal "No CUDA GPU detected. TwinVLA training requires a GPU."
fi

gpu_name=$(python -c "import torch; print(torch.cuda.get_device_name($gpu_id))" 2>/dev/null || echo "unknown")
gpu_vram_mb=$(python -c "import torch; print(torch.cuda.get_device_properties($gpu_id).total_mem // (1024*1024))" 2>/dev/null || echo "0")
gpu_vram_gb=$(( gpu_vram_mb / 1024 ))

info "GPU $gpu_id: $gpu_name ($gpu_vram_gb GB)"
info "Model: $model_type (estimated $vram_estimate)"

# =============================================================================
# Build Training Command
# =============================================================================
section "Training"

mkdir -p "$output_dir"

train_args=(
  "--model_type" "$model_type"
  "--output_dir" "$output_dir"
  "--batch_size" "$batch_size"
  "--learning_rate" "$learning_rate"
  "--max_steps" "$max_steps"
  "--save_steps" "$save_steps"
)

if [[ "$dataset_format" == "lerobot" ]]; then
  train_args+=("--data_type" "lerobot" "--data_root_dir" "$data_source")
else
  train_args+=("--data_type" "rlds" "--data_root_dir" "$dataset_dir" "--data_mix" "robotwin_$task_name")
fi

[[ -n "$wandb_project" ]] && train_args+=("--wandb_project" "$wandb_project")

info "Task: $task_name"
info "Launching: CUDA_VISIBLE_DEVICES=$gpu_id accelerate launch --num_processes 1 scripts/train.py ${train_args[*]}"

cd "$workspace_dir"
CUDA_VISIBLE_DEVICES="$gpu_id" accelerate launch \
  --num_processes 1 \
  scripts/train.py \
  "${train_args[@]}"

# =============================================================================
# Summary
# =============================================================================
section "Training Summary"
print_kv "Task" "$task_name"
print_kv "Model" "$model_type"
print_kv "GPU" "$gpu_name"
print_kv "Steps" "$max_steps"
print_kv "Checkpoints" "$output_dir"
info "Next: evaluate with eval-local-twinvla.sh -c $output_dir -t $task_name"
