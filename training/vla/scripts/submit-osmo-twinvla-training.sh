#!/usr/bin/env bash
# Submit TwinVLA bimanual VLA training workflow to OSMO
# Supports RoboTwin 2.0 and custom LeRobot datasets
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# =============================================================================
# Help
# =============================================================================

show_help() {
  cat << 'EOF'
Usage: submit-osmo-twinvla-training.sh [OPTIONS] [-- osmo-submit-flags]

Submit a TwinVLA bimanual VLA training workflow to OSMO.
Supports RoboTwin 2.0 (RLDS) and custom LeRobot (Parquet+MP4) datasets.

REQUIRED:
    -d, --dataset-path PATH      Dataset path (HF repo ID or RLDS directory)
    -t, --task-name NAME         Task name (e.g., robotwin_open_laptop)

DATA FORMAT:
        --dataset-format FORMAT   Dataset format: rlds, lerobot (default: rlds)

TRAINING OPTIONS:
    -w, --workflow PATH           Workflow template (default: training/vla/workflows/osmo/twinvla-train.yaml)
    -m, --model-type TYPE         VLM backbone: SmolVLM2VLA, Eagle2_1BVLA (default: SmolVLM2VLA)
    -j, --job-name NAME           Job identifier (default: twinvla-{task_name})
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/twinvla)
    -i, --image IMAGE             Container image (default: pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime)
    -g, --num-gpus N              Number of GPUs (default: 2)

TRAINING HYPERPARAMETERS:
        --batch-size N            Training batch size (default: 4)
        --learning-rate LR        Optimizer learning rate (default: 2e-5)
        --max-steps N             Maximum training steps (default: 50000)
        --save-steps N            Checkpoint save frequency (default: 5000)

LOGGING:
        --experiment-name NAME    MLflow experiment name
        --wandb-project NAME      Weights & Biases project name

CHECKPOINT REGISTRATION:
    -r, --register-checkpoint NAME  Model name for Azure ML registration

AZURE CONTEXT:
        --azure-subscription-id ID    Azure subscription ID
        --azure-resource-group NAME   Azure resource group
        --azure-workspace-name NAME   Azure ML workspace

OTHER:
        --use-local-osmo          Use local osmo-dev CLI instead of production osmo
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

EXAMPLES:
    # Train TwinVLA on RoboTwin open_laptop task
    submit-osmo-twinvla-training.sh -d jellyho/robotwin2_rlds -t robotwin_open_laptop

    # Train on LeRobot dataset
    submit-osmo-twinvla-training.sh -d jellyho/aloha_handover_box --dataset-format lerobot

    # Train with larger backbone on 4 GPUs
    submit-osmo-twinvla-training.sh -d jellyho/robotwin2_rlds -t robotwin_open_laptop \
        -m Eagle2_1BVLA -g 4 --batch-size 8
EOF
}

# =============================================================================
# Defaults
# =============================================================================

WORKFLOW="${SCRIPT_DIR}/../workflows/osmo/twinvla-train.yaml"
DATASET_PATH=""
DATASET_FORMAT="rlds"
TASK_NAME=""
MODEL_TYPE="SmolVLM2VLA"
JOB_NAME=""
OUTPUT_DIR="/workspace/outputs/twinvla"
IMAGE="pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime"
NUM_GPUS=2
BATCH_SIZE=4
LEARNING_RATE="2e-5"
MAX_STEPS=50000
SAVE_STEPS=5000
EXPERIMENT_NAME=""
WANDB_PROJECT=""
REGISTER_CHECKPOINT=""
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-}"
AZURE_WORKSPACE_NAME="${AZURE_WORKSPACE_NAME:-}"
USE_LOCAL_OSMO=false
CONFIG_PREVIEW=false

# Extra flags after --
EXTRA_OSMO_FLAGS=()

# =============================================================================
# Parse arguments
# =============================================================================

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) show_help; exit 0 ;;
    -d|--dataset-path)        DATASET_PATH="$2"; shift 2 ;;
    -t|--task-name)           TASK_NAME="$2"; shift 2 ;;
    --dataset-format)         DATASET_FORMAT="$2"; shift 2 ;;
    -w|--workflow)            WORKFLOW="$2"; shift 2 ;;
    -m|--model-type)          MODEL_TYPE="$2"; shift 2 ;;
    -j|--job-name)            JOB_NAME="$2"; shift 2 ;;
    -o|--output-dir)          OUTPUT_DIR="$2"; shift 2 ;;
    -i|--image)               IMAGE="$2"; shift 2 ;;
    -g|--num-gpus)            NUM_GPUS="$2"; shift 2 ;;
    --batch-size)             BATCH_SIZE="$2"; shift 2 ;;
    --learning-rate)          LEARNING_RATE="$2"; shift 2 ;;
    --max-steps)              MAX_STEPS="$2"; shift 2 ;;
    --save-steps)             SAVE_STEPS="$2"; shift 2 ;;
    --experiment-name)        EXPERIMENT_NAME="$2"; shift 2 ;;
    --wandb-project)          WANDB_PROJECT="$2"; shift 2 ;;
    -r|--register-checkpoint) REGISTER_CHECKPOINT="$2"; shift 2 ;;
    --azure-subscription-id)  AZURE_SUBSCRIPTION_ID="$2"; shift 2 ;;
    --azure-resource-group)   AZURE_RESOURCE_GROUP="$2"; shift 2 ;;
    --azure-workspace-name)   AZURE_WORKSPACE_NAME="$2"; shift 2 ;;
    --use-local-osmo)         USE_LOCAL_OSMO=true; shift ;;
    --config-preview)         CONFIG_PREVIEW=true; shift ;;
    --)                       shift; EXTRA_OSMO_FLAGS=("$@"); break ;;
    *)                        fatal "Unknown option: $1. Use --help for usage." ;;
  esac
done

# =============================================================================
# Validation
# =============================================================================

if [[ -z "$DATASET_PATH" ]]; then
  fatal "Missing required --dataset-path. Use --help for usage."
fi

if [[ "$DATASET_FORMAT" == "rlds" ]] && [[ -z "$TASK_NAME" ]]; then
  fatal "RLDS format requires --task-name. Use --help for usage."
fi

if [[ -z "$JOB_NAME" ]]; then
  JOB_NAME="twinvla-${TASK_NAME:-lerobot}-$(date +%Y%m%d-%H%M%S)"
fi

# =============================================================================
# Configuration preview
# =============================================================================

section "TwinVLA Training Configuration"
print_kv "Dataset path"     "$DATASET_PATH"
print_kv "Dataset format"   "$DATASET_FORMAT"
print_kv "Task name"        "$TASK_NAME"
print_kv "Model type"       "$MODEL_TYPE"
print_kv "Job name"         "$JOB_NAME"
print_kv "Image"            "$IMAGE"
print_kv "GPUs"             "$NUM_GPUS"
print_kv "Batch size"       "$BATCH_SIZE"
print_kv "Learning rate"    "$LEARNING_RATE"
print_kv "Max steps"        "$MAX_STEPS"
print_kv "Save steps"       "$SAVE_STEPS"
print_kv "Output dir"       "$OUTPUT_DIR"
print_kv "Workflow"         "$WORKFLOW"

if [[ "$CONFIG_PREVIEW" == "true" ]]; then
  info "Config preview mode — exiting without submission"
  exit 0
fi

# =============================================================================
# Resolve OSMO CLI
# =============================================================================

if [[ "$USE_LOCAL_OSMO" == "true" ]]; then
  OSMO_CMD="osmo-dev"
else
  OSMO_CMD="osmo"
fi

require_tools "$OSMO_CMD"

# =============================================================================
# Submit
# =============================================================================

section "Submitting TwinVLA Training to OSMO"

OSMO_ARGS=(
  workflow submit "$WORKFLOW"
  --set "image=$IMAGE"
  --set "dataset_path=$DATASET_PATH"
  --set "dataset_format=$DATASET_FORMAT"
  --set "task_name=$TASK_NAME"
  --set "model_type=$MODEL_TYPE"
  --set "job_name=$JOB_NAME"
  --set "output_dir=$OUTPUT_DIR"
  --set "num_gpus=$NUM_GPUS"
  --set "batch_size=$BATCH_SIZE"
  --set "learning_rate=$LEARNING_RATE"
  --set "max_steps=$MAX_STEPS"
  --set "save_steps=$SAVE_STEPS"
)

[[ -n "$EXPERIMENT_NAME" ]]       && OSMO_ARGS+=(--set "experiment_name=$EXPERIMENT_NAME")
[[ -n "$WANDB_PROJECT" ]]         && OSMO_ARGS+=(--set "wandb_project=$WANDB_PROJECT")
[[ -n "$REGISTER_CHECKPOINT" ]]   && OSMO_ARGS+=(--set "register_checkpoint=$REGISTER_CHECKPOINT")
[[ -n "$AZURE_SUBSCRIPTION_ID" ]] && OSMO_ARGS+=(--set "azure_subscription_id=$AZURE_SUBSCRIPTION_ID")
[[ -n "$AZURE_RESOURCE_GROUP" ]]  && OSMO_ARGS+=(--set "azure_resource_group=$AZURE_RESOURCE_GROUP")
[[ -n "$AZURE_WORKSPACE_NAME" ]]  && OSMO_ARGS+=(--set "azure_workspace_name=$AZURE_WORKSPACE_NAME")

if [[ ${#EXTRA_OSMO_FLAGS[@]} -gt 0 ]]; then
  OSMO_ARGS+=("${EXTRA_OSMO_FLAGS[@]}")
fi

"$OSMO_CMD" "${OSMO_ARGS[@]}"

# =============================================================================
# Deployment Summary
# =============================================================================

section "Deployment Summary"
print_kv "Job name"     "$JOB_NAME"
print_kv "Dataset"      "$DATASET_PATH"
print_kv "Model"        "$MODEL_TYPE"
print_kv "GPUs"         "$NUM_GPUS"
print_kv "Status"       "Submitted"
