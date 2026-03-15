#!/usr/bin/env bash
# Submit LeRobot behavioral cloning training to Azure ML
# Installs LeRobot dynamically and trains ACT/Diffusion policies from HuggingFace datasets
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

# shellcheck source=../../../shared/lib/common.sh
source "$REPO_ROOT/shared/lib/common.sh"
# shellcheck source=lib/terraform-outputs.sh
source "$SCRIPT_DIR/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

# Source .env file if present (for credentials and Azure context)
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: submit-azureml-lerobot-training.sh [OPTIONS] [-- az-ml-job-flags]

Submit LeRobot behavioral cloning training to Azure ML.

REQUIRED:
    -d, --dataset-repo-id ID     HuggingFace dataset repository (e.g., user/dataset)

AZUREML ASSET OPTIONS:
    --environment-name NAME       AzureML environment name (default: lerobot-training-env)
    --environment-version VER     Environment version (default: 1.0.0)
    --image IMAGE                 Container image (default: pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime)
    --assets-only                 Register environment without submitting job

TRAINING OPTIONS:
    -w, --job-file PATH           Job YAML template (default: training/il/workflows/azureml/lerobot-train.yaml)
    -p, --policy-type TYPE        Policy architecture: act, diffusion (default: act)
    -j, --job-name NAME           Job identifier (default: lerobot-act-training)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/train)
        --policy-repo-id ID       Pre-trained policy for fine-tuning (HuggingFace repo)
        --lerobot-version VER     Specific LeRobot version or "latest" (default: latest)

TRAINING HYPERPARAMETERS:
        --training-steps N        Total training iterations
        --batch-size N            Training batch size
        --eval-freq N             Evaluation frequency
        --save-freq N             Checkpoint save frequency (default: 5000)

LOGGING OPTIONS:
        --wandb-enable            Enable WANDB logging (default)
        --no-wandb                Disable WANDB logging
        --wandb-project NAME      WANDB project name (default: lerobot-training)

CHECKPOINT REGISTRATION:
    -r, --register-checkpoint NAME  Model name for Azure ML registration

AZURE CONTEXT:
        --subscription-id ID      Azure subscription ID
        --resource-group NAME     Azure resource group
        --workspace-name NAME     Azure ML workspace
        --compute TARGET          Compute target override
        --instance-type NAME      Instance type (default: gpuspot)
        --experiment-name NAME    Experiment name override
        --display-name NAME       Display name override
        --stream                  Stream logs after submission

ADVANCED:
        --mlflow-token-retries N  MLflow token refresh retries (default: 3)
        --mlflow-http-timeout N   MLflow HTTP request timeout in seconds (default: 60)

GENERAL:
    -h, --help                    Show this help message
        --config-preview          Print configuration and exit

Values resolved: CLI > Environment variables > Terraform outputs
Additional arguments after -- are forwarded to az ml job create.

EXAMPLES:
    # ACT training with defaults
    submit-azureml-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

    # Diffusion policy with custom hyperparameters
    submit-azureml-lerobot-training.sh \
      -d user/custom-dataset \
      -p diffusion \
      --training-steps 50000 \
      --batch-size 16

    # Register trained model and stream logs
    submit-azureml-lerobot-training.sh \
      -d user/dataset \
      -r my-act-model \
      --stream

    # Fine-tune from pre-trained policy
    submit-azureml-lerobot-training.sh \
      -d user/dataset \
      --policy-repo-id user/pretrained-act \
      --training-steps 10000

    # Register environment only (no job submission)
    submit-azureml-lerobot-training.sh -d placeholder --assets-only
EOF
}

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

ensure_ml_extension() {
  az extension show --name ml &>/dev/null ||
    fatal "Azure ML CLI extension not installed. Run: az extension add --name ml"
}

register_environment() {
  local name="$1" version="$2" image="$3" rg="$4" ws="$5" sub="$6"
  local env_file
  env_file=$(mktemp)

  cat >"$env_file" <<EOF
\$schema: https://azuremlschemas.azureedge.net/latest/environment.schema.json
name: $name
version: $version
image: $image
EOF

  info "Publishing AzureML environment ${name}:${version}"
  az ml environment create --file "$env_file" \
    --name "$name" --version "$version" \
    --resource-group "$rg" --workspace-name "$ws" \
    --subscription "$sub" >/dev/null 2>&1 || \
    warn "Environment ${name}:${version} already exists or registration failed; continuing"
  rm -f "$env_file"
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

environment_name="lerobot-training-env"
environment_version="1.0.0"
image="${IMAGE:-pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime}"
assets_only=false

job_file="$REPO_ROOT/training/il/workflows/azureml/lerobot-train.yaml"
dataset_repo_id="${DATASET_REPO_ID:-}"
policy_type="${POLICY_TYPE:-act}"
job_name="${JOB_NAME:-lerobot-act-training}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/train}"
policy_repo_id="${POLICY_REPO_ID:-}"
lerobot_version="${LEROBOT_VERSION:-}"

training_steps="${TRAINING_STEPS:-}"
batch_size="${BATCH_SIZE:-}"
eval_freq="${EVAL_FREQ:-}"
save_freq="${SAVE_FREQ:-5000}"

wandb_enable="${WANDB_ENABLE:-true}"
wandb_project="${WANDB_PROJECT:-lerobot-training}"
register_checkpoint="${REGISTER_CHECKPOINT:-}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
mlflow_retries="${MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES:-3}"
mlflow_timeout="${MLFLOW_HTTP_REQUEST_TIMEOUT:-60}"

compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
instance_type="gpuspot"
experiment_name=""
display_name=""
stream_logs=false
config_preview=false
forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    --environment-name)           environment_name="$2"; shift 2 ;;
    --environment-version)        environment_version="$2"; shift 2 ;;
    --image|-i)                   image="$2"; shift 2 ;;
    --assets-only)                assets_only=true; shift ;;
    -w|--job-file)                job_file="$2"; shift 2 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -o|--output-dir)              output_dir="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --training-steps)             training_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --eval-freq)                  eval_freq="$2"; shift 2 ;;
    --save-freq)                  save_freq="$2"; shift 2 ;;
    --wandb-enable)               wandb_enable="true"; shift ;;
    --no-wandb)                   wandb_enable="false"; shift ;;
    --wandb-project)              wandb_project="$2"; shift 2 ;;
    -r|--register-checkpoint)     register_checkpoint="$2"; shift 2 ;;
    --subscription-id)            subscription_id="$2"; shift 2 ;;
    --resource-group)             resource_group="$2"; shift 2 ;;
    --workspace-name)             workspace_name="$2"; shift 2 ;;
    --mlflow-token-retries)       mlflow_retries="$2"; shift 2 ;;
    --mlflow-http-timeout)        mlflow_timeout="$2"; shift 2 ;;
    --compute)                    compute="$2"; shift 2 ;;
    --instance-type)              instance_type="$2"; shift 2 ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    --display-name)               display_name="$2"; shift 2 ;;
    --stream)                     stream_logs=true; shift ;;
    --config-preview)             config_preview=true; shift ;;
    --)                           shift; forward_args=("$@"); break ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools az
ensure_ml_extension

[[ -z "$dataset_repo_id" ]] && fatal "--dataset-repo-id is required"
[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

case "$policy_type" in
  act|diffusion) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion)" ;;
esac

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Dataset" "$dataset_repo_id"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Name" "$job_name"
  print_kv "Image" "$image"
  print_kv "Output Dir" "$output_dir"
  print_kv "Training Steps" "${training_steps:-<default>}"
  print_kv "Batch Size" "${batch_size:-<default>}"
  print_kv "Save Freq" "$save_freq"
  print_kv "WANDB" "$wandb_enable"
  print_kv "Register Model" "${register_checkpoint:-<none>}"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Compute" "${compute:-<not set>}"
  print_kv "Instance Type" "$instance_type"
  print_kv "Environment" "${environment_name}:${environment_version}"
  exit 0
fi

#------------------------------------------------------------------------------
# Register Environment
#------------------------------------------------------------------------------

register_environment "$environment_name" "$environment_version" "$image" \
  "$resource_group" "$workspace_name" "$subscription_id"

info "Environment: ${environment_name}:${environment_version}"

if [[ "$assets_only" == "true" ]]; then
  info "Assets prepared; skipping job submission per --assets-only"
  exit 0
fi

#------------------------------------------------------------------------------
# Pre-submission Checks
#------------------------------------------------------------------------------

[[ -f "$job_file" ]] || fatal "Job file not found: $job_file"

#------------------------------------------------------------------------------
# Build Training Command
#
# The AzureML job runs a training entry script that:
# 1. Installs LeRobot dependencies from the workflow manifest
# 2. Authenticates with HuggingFace Hub
# 3. Configures MLflow tracking
# 4. Runs lerobot-train with appropriate arguments
# 5. Registers the final checkpoint to Azure ML
#------------------------------------------------------------------------------

# Build the inline training command
train_cmd='bash -c '"'"'
set -euo pipefail

echo "=== LeRobot AzureML Training ==="

# Install runtime dependencies from workflow manifest
apt-get update -qq && apt-get install -y -qq ffmpeg git build-essential > /dev/null 2>&1
pip install --quiet uv
uv pip compile training/lerobot/pyproject.toml -o /tmp/lerobot-runtime-requirements.txt
uv pip install --system --requirement /tmp/lerobot-runtime-requirements.txt

# HuggingFace auth
if [[ -n "${HF_TOKEN:-}" ]]; then
  python3 -c "from huggingface_hub import login; login(token=\"${HF_TOKEN}\", add_to_git_credential=False)"
fi

# Build lerobot-train args
train_args=(
  --dataset.repo_id="${DATASET_REPO_ID}"
  --policy.type="${POLICY_TYPE}"
  --output_dir="${OUTPUT_DIR}"
  --job_name="${JOB_NAME}"
  --policy.device=cuda
)

if [[ "${WANDB_ENABLE:-true}" == "true" ]]; then
  train_args+=(--wandb.enable=true)
  [[ -n "${WANDB_PROJECT:-}" ]] && train_args+=(--wandb.project="${WANDB_PROJECT}")
else
  train_args+=(--wandb.enable=false)
fi

[[ -n "${POLICY_REPO_ID:-}" ]] && train_args+=(--policy.repo_id="${POLICY_REPO_ID}")
[[ -n "${TRAINING_STEPS:-}" ]] && train_args+=(--steps="${TRAINING_STEPS}")
[[ -n "${BATCH_SIZE:-}" ]] && train_args+=(--batch_size="${BATCH_SIZE}")
[[ -n "${EVAL_FREQ:-}" ]] && train_args+=(--eval_freq="${EVAL_FREQ}")
[[ -n "${SAVE_FREQ:-}" ]] && train_args+=(--save_freq="${SAVE_FREQ}")

echo "Running: lerobot-train ${train_args[*]}"
lerobot-train "${train_args[@]}"

echo "=== Training Complete ==="

# Register checkpoint
if [[ -n "${REGISTER_CHECKPOINT:-}" ]]; then
  echo "Registering checkpoint to Azure ML..."
  python3 -c "
import os, sys
from pathlib import Path
from azure.ai.ml import MLClient
from azure.ai.ml.entities import Model
from azure.ai.ml.constants import AssetTypes
from azure.identity import DefaultAzureCredential

output_dir = Path(os.environ[\"OUTPUT_DIR\"])
checkpoint_dirs = sorted(output_dir.glob(\"checkpoints/*\"), key=lambda p: p.stat().st_mtime, reverse=True)
if not checkpoint_dirs:
    pretrained = output_dir / \"pretrained_model\"
    checkpoint_path = pretrained if pretrained.exists() else None
else:
    pretrained = checkpoint_dirs[0] / \"pretrained_model\"
    checkpoint_path = pretrained if pretrained.exists() else checkpoint_dirs[0]

if not checkpoint_path:
    print(\"No checkpoints found\"); sys.exit(0)

policy_type = os.environ.get(\"POLICY_TYPE\", \"act\")
credential = DefaultAzureCredential()
client = MLClient(credential, os.environ[\"AZURE_SUBSCRIPTION_ID\"], os.environ[\"AZURE_RESOURCE_GROUP\"], os.environ[\"AZUREML_WORKSPACE_NAME\"])
model = Model(
    path=str(checkpoint_path),
    name=os.environ[\"REGISTER_CHECKPOINT\"],
    description=\"LeRobot %s policy\" % policy_type,
    type=AssetTypes.CUSTOM_MODEL,
    tags={\"framework\": \"lerobot\", \"policy_type\": policy_type, \"source\": \"azureml-job\"},
)
registered = client.models.create_or_update(model)
print(f\"Model registered: {registered.name} v{registered.version}\")
"
fi
'"'"''

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

az_args=(
  az ml job create
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --file "$job_file"
  --set "environment=azureml:${environment_name}:${environment_version}"
)

[[ -n "$compute" ]] && az_args+=(--set "compute=$compute")
[[ -n "$instance_type" ]] && az_args+=(--set "resources.instance_type=$instance_type")
[[ -n "$experiment_name" ]] && az_args+=(--set "experiment_name=$experiment_name")
[[ -n "$display_name" ]] && az_args+=(--set "display_name=$display_name")

az_args+=(--set "command=$train_cmd")

# Input values
az_args+=(
  --set "inputs.dataset_repo_id=$dataset_repo_id"
  --set "inputs.policy_type=$policy_type"
  --set "inputs.job_name=$job_name"
  --set "inputs.output_dir=$output_dir"
  --set "inputs.save_freq=$save_freq"
  --set "inputs.wandb_enable=$wandb_enable"
  --set "inputs.wandb_project=$wandb_project"
  --set "inputs.subscription_id=$subscription_id"
  --set "inputs.resource_group=$resource_group"
  --set "inputs.workspace_name=$workspace_name"
  --set "inputs.mlflow_token_refresh_retries=$mlflow_retries"
  --set "inputs.mlflow_http_request_timeout=$mlflow_timeout"
)

[[ -n "$policy_repo_id" ]]      && az_args+=(--set "inputs.policy_repo_id=$policy_repo_id")
[[ -n "$lerobot_version" ]]     && az_args+=(--set "inputs.lerobot_version=$lerobot_version")
[[ -n "$training_steps" ]]      && az_args+=(--set "inputs.training_steps=$training_steps")
[[ -n "$batch_size" ]]          && az_args+=(--set "inputs.batch_size=$batch_size")
[[ -n "$eval_freq" ]]           && az_args+=(--set "inputs.eval_freq=$eval_freq")
[[ -n "$register_checkpoint" ]] && az_args+=(--set "inputs.register_checkpoint=$register_checkpoint")

# Environment variables
az_args+=(
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
)

[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
az_args+=(--query "name" -o "tsv")

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting AzureML LeRobot training job..."
info "  Dataset: $dataset_repo_id"
info "  Policy: $policy_type"
info "  Job Name: $job_name"
info "  Image: $image"

job_result=$("${az_args[@]}") || fatal "Job submission failed"

info "Job submitted: $job_result"
info "Portal: https://ml.azure.com/runs/$job_result?wsid=/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.MachineLearningServices/workspaces/$workspace_name"

if [[ "$stream_logs" == "true" ]]; then
  info "Streaming job logs (Ctrl+C to stop)..."
  az ml job stream --name "$job_result" \
    --resource-group "$resource_group" --workspace-name "$workspace_name" || true
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Job Name" "$job_result"
print_kv "Dataset" "$dataset_repo_id"
print_kv "Policy Type" "$policy_type"
print_kv "Image" "$image"
print_kv "Compute" "${compute:-<not set>}"
print_kv "Instance Type" "$instance_type"
print_kv "Environment" "${environment_name}:${environment_version}"
print_kv "Workspace" "$workspace_name"
