#!/usr/bin/env bash
# Submit OSMO training workflow using dataset folder injection
# Uploads training code via OSMO localpath and registers as versioned dataset
# Excludes __pycache__ and build artifacts via staging directory
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || cd "$SCRIPT_DIR/../../.." && pwd)"
TMP_DIR="$SCRIPT_DIR/.tmp"
STAGING_DIR="$TMP_DIR/osmo-dataset-staging"

source "$REPO_ROOT/shared/lib/common.sh"
# shellcheck source=../../../shared/lib/terraform-outputs.sh
source "$REPO_ROOT/shared/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: submit-osmo-dataset-training.sh [OPTIONS] [-- osmo-submit-flags]

Submit an OSMO training workflow using dataset folder injection.
The training folder is uploaded as a versioned OSMO dataset via localpath.

WORKFLOW OPTIONS:
    -w, --workflow PATH           Workflow template (default: training/rl/workflows/osmo/train-dataset.yaml)
    -t, --task NAME               IsaacLab task (default: Isaac-Velocity-Rough-Anymal-C-v0)
    -n, --num-envs COUNT          Number of environments (default: 2048)
    -m, --max-iterations N        Maximum iterations (empty to unset)
    -i, --image IMAGE             Container image (default: nvcr.io/nvidia/isaac-lab:2.3.2)
    -b, --backend BACKEND         Training backend: skrl (default), rsl_rl

RESOURCE OPTIONS:
        --gpu COUNT               Number of GPUs (default: 1)
        --cpu COUNT               CPU cores (default: 30)
        --memory SIZE             Memory with unit (default: 400Gi)
        --storage SIZE            Storage with unit (default: 200Gi)

DATASET OPTIONS:
        --dataset-bucket NAME     OSMO bucket name (default: training)
        --dataset-name NAME       Dataset name (default: training-code)
        --training-path PATH      Local path to upload (default: training/rl)

CHECKPOINT OPTIONS:
    -c, --checkpoint-uri URI      MLflow checkpoint artifact URI
    -M, --checkpoint-mode MODE    from-scratch, warm-start, resume, fresh (default: from-scratch)
    -r, --register-checkpoint NAME  Model name for checkpoint registration
        --skip-register-checkpoint  Skip automatic model registration

AZURE CONTEXT:
        --azure-subscription-id ID    Azure subscription ID
        --azure-resource-group NAME   Azure resource group
        --azure-workspace-name NAME   Azure ML workspace

OTHER:
    -s, --run-smoke-test          Enable Azure connectivity smoke test
        --use-local-osmo          Use local osmo-dev CLI instead of production osmo
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

Values resolved: CLI > Environment variables > Terraform outputs
Additional arguments after -- are forwarded to osmo workflow submit.
EOF
}

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

derive_model_name() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

normalize_checkpoint_mode() {
  local mode="$1"
  [[ -z "$mode" ]] && { echo "from-scratch"; return; }
  lowered=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
  case "$lowered" in
    from-scratch|warm-start|resume) echo "$lowered" ;;
    fresh) echo "from-scratch" ;;
    *) fatal "Unsupported checkpoint mode: $mode" ;;
  esac
}

# Build rsync exclude arguments from ignore file (gitignore syntax)
build_rsync_excludes() {
  local ignore_file="${1:?ignore file required}"
  local excludes=()
  [[ -f "$ignore_file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Skip comments and empty lines
    [[ -z "$line" || "$line" == \#* ]] && continue
    # Strip trailing slashes for rsync compatibility
    line="${line%/}"
    excludes+=("--exclude=$line")
  done < "$ignore_file"
  printf '%s\n' "${excludes[@]}"
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

workflow="$REPO_ROOT/training/rl/workflows/osmo/train-dataset.yaml"
task="${TASK:-Isaac-Velocity-Rough-Anymal-C-v0}"
num_envs="${NUM_ENVS:-2048}"
max_iterations="${MAX_ITERATIONS:-}"
image="${IMAGE:-nvcr.io/nvidia/isaac-lab:2.3.2}"
backend="${TRAINING_BACKEND:-skrl}"

gpu="${OSMO_GPU:-1}"
cpu="${OSMO_CPU:-30}"
memory="${OSMO_MEMORY:-400Gi}"
storage="${OSMO_STORAGE:-200Gi}"

# Dataset configuration
dataset_bucket="${OSMO_DATASET_BUCKET:-training}"
dataset_name="${OSMO_DATASET_NAME:-training-code}"
training_path="${TRAINING_PATH:-$REPO_ROOT/training/rl}"

checkpoint_uri="${CHECKPOINT_URI:-}"
checkpoint_mode="${CHECKPOINT_MODE:-from-scratch}"
register_checkpoint="${REGISTER_CHECKPOINT:-}"
skip_register=false

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

run_smoke="${RUN_AZURE_SMOKE_TEST:-0}"
use_local_osmo=false
config_preview=false
forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    -w|--workflow)                workflow="$2"; shift 2 ;;
    -t|--task)                    task="$2"; shift 2 ;;
    -n|--num-envs)                num_envs="$2"; shift 2 ;;
    -m|--max-iterations)          max_iterations="$2"; shift 2 ;;
    -i|--image)                   image="$2"; shift 2 ;;
    -b|--backend)                 backend="$2"; shift 2 ;;
    --gpu)                        gpu="$2"; shift 2 ;;
    --cpu)                        cpu="$2"; shift 2 ;;
    --memory)                     memory="$2"; shift 2 ;;
    --storage)                    storage="$2"; shift 2 ;;
    --dataset-bucket)             dataset_bucket="$2"; shift 2 ;;
    --dataset-name)               dataset_name="$2"; shift 2 ;;
    --training-path)              training_path="$2"; shift 2 ;;
    -c|--checkpoint-uri)          checkpoint_uri="$2"; shift 2 ;;
    -M|--checkpoint-mode)         checkpoint_mode="$2"; shift 2 ;;
    -r|--register-checkpoint)     register_checkpoint="$2"; shift 2 ;;
    --skip-register-checkpoint)   skip_register=true; shift ;;
    --azure-subscription-id)      subscription_id="$2"; shift 2 ;;
    --azure-resource-group)       resource_group="$2"; shift 2 ;;
    --azure-workspace-name)       workspace_name="$2"; shift 2 ;;
    -s|--run-smoke-test)          run_smoke="1"; shift ;;
    --use-local-osmo)             use_local_osmo=true; shift ;;
    --config-preview)             config_preview=true; shift ;;
    --)                           shift; forward_args=("$@"); break ;;
    *)                            forward_args+=("$1"); shift ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools osmo rsync

[[ -f "$workflow" ]] || fatal "Workflow template not found: $workflow"
[[ -d "$training_path" ]] || fatal "Training directory not found: $training_path"

checkpoint_mode="$(normalize_checkpoint_mode "$checkpoint_mode")"

if [[ "$skip_register" == "false" && -z "$register_checkpoint" ]]; then
  register_checkpoint="$(derive_model_name "$task")"
  info "Auto-derived model name: $register_checkpoint"
fi

[[ "$skip_register" == "true" ]] && register_checkpoint=""

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Task" "$task"
  print_kv "Backend" "$backend"
  print_kv "Image" "$image"
  print_kv "Num Envs" "$num_envs"
  print_kv "Max Iterations" "${max_iterations:-<not set>}"
  print_kv "GPU" "$gpu"
  print_kv "CPU" "$cpu"
  print_kv "Memory" "$memory"
  print_kv "Storage" "$storage"
  print_kv "Dataset Bucket" "$dataset_bucket"
  print_kv "Dataset Name" "$dataset_name"
  print_kv "Training Path" "$training_path"
  print_kv "Checkpoint Mode" "$checkpoint_mode"
  print_kv "Checkpoint URI" "${checkpoint_uri:-<not set>}"
  print_kv "Register Model" "${register_checkpoint:-<none>}"
  print_kv "Workflow" "$workflow"
  print_kv "Subscription" "${subscription_id:-<not set>}"
  print_kv "Resource Group" "${resource_group:-<not set>}"
  print_kv "Workspace" "${workspace_name:-<not set>}"
  exit 0
fi

#------------------------------------------------------------------------------
# Stage Training Folder (exclude cache and build artifacts)
#------------------------------------------------------------------------------

info "Staging training folder (excluding cache/build artifacts)..."
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

# Build exclude args from .amlignore (uses gitignore syntax)
amlignore_file="$REPO_ROOT/training/.amlignore"
rsync_excludes=()
if [[ -f "$amlignore_file" ]]; then
  while IFS= read -r exclude; do
    rsync_excludes+=("$exclude")
  done < <(build_rsync_excludes "$amlignore_file")
fi

rsync -a --delete "${rsync_excludes[@]}" "$training_path/" "$STAGING_DIR/training/"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

info "Submitting workflow with dataset folder injection..."
info "  Training path: $training_path (staged)"
info "  Dataset: $dataset_bucket/$dataset_name"
info "  Task: $task"
info "  Backend: $backend"
info "  Image: $image"

# Convert staged path to relative path from workflow location for localpath
workflow_dir="$(dirname "$workflow")"
rel_training_path="$(python3 -c "import os.path; print(os.path.relpath('$STAGING_DIR/training', '$workflow_dir'))")"

submit_args=(
  workflow submit "$workflow"
  --set-string "image=$image"
  "task=$task"
  "num_envs=$num_envs"
  "run_azure_smoke_test=$run_smoke"
  "checkpoint_uri=$checkpoint_uri"
  "checkpoint_mode=$checkpoint_mode"
  "register_checkpoint=$register_checkpoint"
  "training_backend=$backend"
  "gpu=$gpu"
  "cpu=$cpu"
  "memory=$memory"
  "storage=$storage"
  "dataset_bucket=$dataset_bucket"
  "dataset_name=$dataset_name"
  "training_localpath=$rel_training_path"
)

[[ -n "$subscription_id" ]] && submit_args+=("azure_subscription_id=$subscription_id")
[[ -n "$resource_group" ]] && submit_args+=("azure_resource_group=$resource_group")
[[ -n "$workspace_name" ]] && submit_args+=("azure_workspace_name=$workspace_name")

if [[ -n "$max_iterations" ]]; then
  submit_args+=("max_iterations=$max_iterations")
else
  submit_args+=("max_iterations=")
fi

[[ ${#forward_args[@]} -gt 0 ]] && submit_args+=("${forward_args[@]}")

#------------------------------------------------------------------------------
# Submit Workflow
#------------------------------------------------------------------------------

osmo "${submit_args[@]}" || fatal "Failed to submit workflow"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Task" "$task"
print_kv "Backend" "$backend"
print_kv "Image" "$image"
print_kv "Num Envs" "$num_envs"
print_kv "GPU" "$gpu"
print_kv "Dataset" "$dataset_bucket/$dataset_name"
print_kv "Checkpoint Mode" "$checkpoint_mode"
print_kv "Register Model" "${register_checkpoint:-<none>}"
print_kv "Workflow" "$workflow"

info "Workflow submitted successfully"
info "Dataset uploaded: $dataset_bucket/$dataset_name"
