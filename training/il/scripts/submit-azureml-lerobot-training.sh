#!/usr/bin/env bash
# Submit LeRobot behavioral cloning training to Azure ML
# Installs LeRobot dynamically and trains ACT/Diffusion policies from HuggingFace datasets
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
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

OPTIONS:
    -d, --dataset-repo-id ID     Dataset logical name for folder naming (default: dataset)

DATA SOURCE:
        --blob-url URL                Add blob dataset URL (repeatable; use multiple times for merge)
        --dataset-root DIR            Container path where datasets are materialized
                                      (default: /workspace/data)

AZUREML ASSET OPTIONS:
    --environment-name NAME       AzureML environment name (default: lerobot-training-env)
    --environment-version VER     Environment version (default: 1.0.0)
    --image IMAGE                 Container image (default: pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime)
    --assets-only                 Register environment without submitting job

TRAINING OPTIONS:
    -w, --job-file PATH           Job YAML template (default: training/il/workflows/azureml/lerobot-train.yaml)
    -p, --policy-type TYPE        Policy architecture: act, diffusion (default: act)
    -j, --job-name NAME           Job identifier (default: lerobot-act-training)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/train)
        --policy-repo-id ID       Pre-trained policy for fine-tuning (HuggingFace repo)
        --init-from-policy-model URI
                                  Warm-start weights from a previously registered AzureML
                                  model. Accepted forms:
                                    azureml:NAME:VERSION       (numeric version required)
                                    azureml://.../models/NAME/versions/VERSION
                                    https://...blob.core.windows.net/...
                                  Shorthands like azureml:NAME or azureml:NAME@latest
                                  are rejected to keep runs reproducible.
                                  Optimizer, scheduler, and step counter start fresh.
                                  Mutually exclusive with --policy-repo-id.
        --lerobot-version VER     Specific LeRobot version or "latest" (default: latest)

TRAINING HYPERPARAMETERS:
        --training-steps N        Total training iterations
        --batch-size N            Training batch size
        --eval-freq N             Evaluation frequency
        --save-freq N             Checkpoint save frequency (default: 5000)

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
    -a, --save-as PATH            Write created job state YAML to PATH

ADVANCED:
        --mlflow-token-retries N  MLflow token refresh retries (default: 3)
        --mlflow-http-timeout N   MLflow HTTP request timeout in seconds (default: 60)

GENERAL:
    -h, --help                    Show this help message
        --config-preview          Print configuration and exit

Values resolved: CLI > Environment variables > Terraform outputs
Additional arguments after -- are forwarded to az ml job create.

EXAMPLES:
    # ACT training with HuggingFace dataset
    submit-azureml-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

    # Diffusion policy with custom hyperparameters
    submit-azureml-lerobot-training.sh \
      -d user/custom-dataset \
      -p diffusion \
      --training-steps 50000 \
      --batch-size 16

    # Warm-start a new run from a previously registered checkpoint
    submit-azureml-lerobot-training.sh \
      -d user/dataset \
      --init-from-policy-model azureml:lerobot-act:7

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

    # Single blob dataset
    submit-azureml-lerobot-training.sh \
      --blob-url "https://mystorageaccount.blob.core.windows.net/datasets/pusht" \
      -r pusht-model

    # Multiple blob datasets (merged)
    submit-azureml-lerobot-training.sh \
      --blob-url "https://account1.blob.core.windows.net/train/pusht" \
      --blob-url "https://account2.blob.core.windows.net/val/pusht" \
      -r merged-pusht-model

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
image="${IMAGE:-pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime}"
assets_only=false

job_file="$REPO_ROOT/training/il/workflows/azureml/lerobot-train.yaml"
dataset_repo_id="${DATASET_REPO_ID:-}"
policy_type="${POLICY_TYPE:-act}"
job_name="${JOB_NAME:-lerobot-act-training}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/train}"
policy_repo_id="${POLICY_REPO_ID:-}"
init_from_policy_model="${INIT_FROM_POLICY_MODEL:-}"
lerobot_version="${LEROBOT_VERSION:-}"

blob_urls=()
dataset_root="${DATASET_ROOT:-/workspace/data}"

training_steps="${TRAINING_STEPS:-}"
batch_size="${BATCH_SIZE:-}"
eval_freq="${EVAL_FREQ:-}"
save_freq="${SAVE_FREQ:-5000}"

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
save_as=""
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
    --init-from-policy-model)     init_from_policy_model="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --blob-url)                   blob_urls+=("$2"); shift 2 ;;
    --dataset-root)               dataset_root="$2"; shift 2 ;;
    --training-steps)             training_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --eval-freq)                  eval_freq="$2"; shift 2 ;;
    --save-freq)                  save_freq="$2"; shift 2 ;;
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
    -a|--save-as)                 save_as="$2"; shift 2 ;;
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

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

if [[ ${#blob_urls[@]} -eq 0 ]]; then
  [[ -z "$dataset_repo_id" ]] && fatal "--dataset-repo-id is required (or provide --blob-url for blob storage)"
else
  dataset_repo_id="${dataset_repo_id:-dataset}"
fi

case "$policy_type" in
  act|diffusion) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion)" ;;
esac

if [[ -n "$init_from_policy_model" && -n "$policy_repo_id" ]]; then
  fatal "--init-from-policy-model and --policy-repo-id are mutually exclusive"
fi

# Accept only fully-qualified, version-pinned URIs. Reject @latest and bare
# azureml:NAME so reruns of the same job spec do not silently drift to a newer
# registered model.
if [[ -n "$init_from_policy_model" ]]; then
  case "$init_from_policy_model" in
    azureml://*/models/*/versions/[0-9]*) ;;
    https://*) ;;
    azureml:*:*)
      version="${init_from_policy_model##*:}"
      [[ "$version" =~ ^[0-9]+$ ]] || fatal \
        "--init-from-policy-model: version must be numeric (got '$init_from_policy_model'). Use azureml:NAME:VERSION; @latest and shorthands are not accepted."
      ;;
    *)
      fatal "--init-from-policy-model: unsupported URI form '$init_from_policy_model'. Use azureml:NAME:VERSION, azureml://.../models/NAME/versions/VERSION, or https://..."
      ;;
  esac
fi

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
  print_kv "Register Model" "${register_checkpoint:-<none>}"
  print_kv "Init From Model" "${init_from_policy_model:-<none>}"
  if [[ ${#blob_urls[@]} -gt 0 ]]; then
    print_kv "Blob URLs" "${#blob_urls[@]} dataset(s)"
    print_kv "Dataset Root" "$dataset_root"
  else
    print_kv "Data Source" "HuggingFace Hub"
  fi
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
# The AzureML job runs training/il/scripts/lerobot/azureml-train-entry.sh, which
# is uploaded as part of the code asset. Keeping the inline command short avoids
# multi-line YAML escaping issues with the Azure ML K8s extension.
#------------------------------------------------------------------------------

train_cmd="bash il/scripts/lerobot/azureml-train-entry.sh"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

az_args=(
  az ml job create
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --file "$job_file"
  --set "code=$REPO_ROOT/training"
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
  --set "inputs.subscription_id=$subscription_id"
  --set "inputs.resource_group=$resource_group"
  --set "inputs.workspace_name=$workspace_name"
  --set "inputs.mlflow_token_refresh_retries=$mlflow_retries"
  --set "inputs.mlflow_http_request_timeout=$mlflow_timeout"
)

[[ -n "$policy_repo_id" ]]      && az_args+=(--set "inputs.policy_repo_id=$policy_repo_id")
if [[ -n "$init_from_policy_model" ]]; then
  az_args+=(
    --set "inputs.init_from_policy_model.type=custom_model"
    --set "inputs.init_from_policy_model.mode=download"
    --set "inputs.init_from_policy_model.path=$init_from_policy_model"
  )
fi
[[ -n "$lerobot_version" ]]     && az_args+=(--set "inputs.lerobot_version=$lerobot_version")
[[ -n "$training_steps" ]]      && az_args+=(--set "inputs.training_steps=$training_steps")
[[ -n "$batch_size" ]]          && az_args+=(--set "inputs.batch_size=$batch_size")
[[ -n "$eval_freq" ]]           && az_args+=(--set "inputs.eval_freq=$eval_freq")
[[ -n "$register_checkpoint" ]] && az_args+=(--set "inputs.register_checkpoint=$register_checkpoint")

if [[ ${#blob_urls[@]} -gt 0 ]]; then
  blob_urls_json=$(python3 -c "import json; import sys; print(json.dumps(sys.argv[1:]))" "${blob_urls[@]}")
  az_args+=(--set "inputs.blob_urls=$blob_urls_json")
  az_args+=(--set "inputs.dataset_root=$dataset_root")
fi

# Environment variables
#
# The Azure ML Kubernetes extension does not substitute `${{inputs.X}}` template
# refs in `environment_variables` at runtime: it passes the literal string into
# the container. Set every env var the entry script reads directly via
# `--set environment_variables.X=Y` so the values are baked into the job spec.
az_args+=(
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
  --set "environment_variables.DATASET_REPO_ID=$dataset_repo_id"
  --set "environment_variables.POLICY_TYPE=$policy_type"
  --set "environment_variables.JOB_NAME=$job_name"
  --set "environment_variables.OUTPUT_DIR=$output_dir"
  --set "environment_variables.SAVE_FREQ=$save_freq"
)

[[ -n "$policy_repo_id" ]]      && az_args+=(--set "environment_variables.POLICY_REPO_ID=$policy_repo_id")
[[ -n "$init_from_policy_model" ]] && az_args+=(--set "environment_variables.INIT_FROM_POLICY_MODEL_SOURCE=$init_from_policy_model")
[[ -n "$lerobot_version" ]]     && az_args+=(--set "environment_variables.LEROBOT_VERSION=$lerobot_version")
[[ -n "$training_steps" ]]      && az_args+=(--set "environment_variables.TRAINING_STEPS=$training_steps")
[[ -n "$batch_size" ]]          && az_args+=(--set "environment_variables.BATCH_SIZE=$batch_size")
[[ -n "$eval_freq" ]]           && az_args+=(--set "environment_variables.EVAL_FREQ=$eval_freq")
[[ -n "$register_checkpoint" ]] && az_args+=(--set "environment_variables.REGISTER_CHECKPOINT=$register_checkpoint")

if [[ ${#blob_urls[@]} -gt 0 ]]; then
  blob_urls_json=$(python3 -c "import json; import sys; print(json.dumps(sys.argv[1:]))" "${blob_urls[@]}")
  az_args+=(--set "environment_variables.BLOB_URLS=$blob_urls_json")
  az_args+=(--set "environment_variables.DATASET_ROOT=$dataset_root")
fi

[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
[[ -n "$save_as" ]] && az_args+=(--save-as "$save_as")
az_args+=(--query "name" -o "tsv")

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting AzureML LeRobot training job..."
info "  Dataset: $dataset_repo_id"
info "  Policy: $policy_type"
info "  Job Name: $job_name"
info "  Image: $image"
[[ ${#blob_urls[@]} -gt 0 ]] && info "  Data Source: Blob URLs (${#blob_urls[@]} dataset(s))"

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
[[ ${#blob_urls[@]} -gt 0 ]] && print_kv "Blob Datasets" "${#blob_urls[@]}"
[[ -n "$init_from_policy_model" ]] && print_kv "Init From Model" "$init_from_policy_model"
[[ -n "$save_as" ]] && print_kv "Saved Job YAML" "$save_as"
