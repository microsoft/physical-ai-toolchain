#!/usr/bin/env bash
# Submit LeRobot inference/evaluation to Azure ML
# Evaluates trained policies from AzureML model registry or HuggingFace Hub
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

# shellcheck source=../deploy/002-setup/lib/common.sh
source "$REPO_ROOT/deploy/002-setup/lib/common.sh"
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
Usage: submit-azureml-lerobot-inference.sh [OPTIONS] [-- az-ml-job-flags]

Submit LeRobot inference/evaluation to Azure ML.
Evaluates trained policies from AzureML model registry or HuggingFace Hub.

POLICY SOURCE (one required):
        --policy-repo-id ID       HuggingFace policy repository (e.g., user/trained-policy)
        --from-aml-model          Load policy from AzureML model registry
        --model-name NAME         AzureML model registry name (e.g., hex-pickup-act)
        --model-version VERSION   AzureML model version (e.g., 4)

DATASET SOURCE:
    -d, --dataset-repo-id ID     HuggingFace dataset for replay evaluation
        --from-blob               Download dataset from Azure Blob Storage
        --storage-account NAME    Azure storage account (default: from Terraform)
        --storage-container NAME  Blob container name (default: datasets)
        --blob-prefix PREFIX      Blob path prefix (e.g., lerobot)

AZUREML ASSET OPTIONS:
        --environment-name NAME   AzureML environment name (default: lerobot-inference-env)
        --environment-version VER Environment version (default: 1.0.0)
    -i, --image IMAGE             Container image (default: pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime)
        --assets-only             Register environment without submitting job

EVALUATION OPTIONS:
    -w, --job-file PATH           Job YAML template (default: workflows/azureml/lerobot-infer.yaml)
    -p, --policy-type TYPE        Policy architecture: act, diffusion (default: act)
    -j, --job-name NAME           Job identifier (default: lerobot-eval)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/eval)
        --lerobot-version VER     Specific LeRobot version or "latest" (default: latest)
        --eval-episodes N         Number of evaluation episodes (default: 10)
        --eval-batch-size N       Evaluation batch size (default: 10)
        --record-video            Record evaluation videos

LOGGING:
        --mlflow-enable           Enable MLflow logging with trajectory plots to AzureML
        --experiment-name NAME    MLflow experiment name (default: auto-derived)

MODEL REGISTRATION:
    -r, --register-model NAME     Model name for Azure ML registration

AZURE CONTEXT:
        --subscription-id ID      Azure subscription ID
        --resource-group NAME     Azure resource group
        --workspace-name NAME     Azure ML workspace
        --compute TARGET          Compute target override
        --instance-type NAME      Instance type (default: gpuspot)
        --display-name NAME       Display name override
        --stream                  Stream logs after submission

ADVANCED:
        --mlflow-token-retries N  MLflow token refresh retries (default: 5)
        --mlflow-http-timeout N   MLflow HTTP request timeout in seconds (default: 600)

GENERAL:
    -h, --help                    Show this help message
        --config-preview          Print configuration and exit

Values resolved: CLI > Environment variables > Terraform outputs
Additional arguments after -- are forwarded to az ml job create.

EXAMPLES:
    # Evaluate an AzureML-registered model against blob dataset
    submit-azureml-lerobot-inference.sh \
      --from-aml-model \
      --model-name hex-pickup-act \
      --model-version 3 \
      --from-blob \
      --storage-account stosmorbt3dev001 \
      --blob-prefix lerobot \
      --mlflow-enable \
      --eval-episodes 10

    # Evaluate a HuggingFace policy
    submit-azureml-lerobot-inference.sh \
      --policy-repo-id user/trained-act \
      -d lerobot/aloha_sim_insertion_human

    # Register environment only (no job submission)
    submit-azureml-lerobot-inference.sh --assets-only
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

environment_name="lerobot-inference-env"
environment_version="1.0.0"
image="${IMAGE:-pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime}"
assets_only=false

job_file="$REPO_ROOT/workflows/azureml/lerobot-infer.yaml"
policy_repo_id="${POLICY_REPO_ID:-}"
policy_type="${POLICY_TYPE:-act}"
dataset_repo_id="${DATASET_REPO_ID:-}"
job_name="${JOB_NAME:-lerobot-eval}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/eval}"
lerobot_version="${LEROBOT_VERSION:-}"

eval_episodes="${EVAL_EPISODES:-10}"
eval_batch_size="${EVAL_BATCH_SIZE:-10}"
record_video="${RECORD_VIDEO:-false}"
mlflow_enable="${MLFLOW_ENABLE:-false}"
experiment_name="${EXPERIMENT_NAME:-}"
register_model="${REGISTER_MODEL:-}"

from_aml_model=false
model_name="${AML_MODEL_NAME:-}"
model_version="${AML_MODEL_VERSION:-}"
from_blob=false
storage_account="${BLOB_STORAGE_ACCOUNT:-${AZURE_STORAGE_ACCOUNT_NAME:-}}"
storage_container="${BLOB_STORAGE_CONTAINER:-datasets}"
blob_prefix="${BLOB_PREFIX:-}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
mlflow_retries="${MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES:-5}"
mlflow_timeout="${MLFLOW_HTTP_REQUEST_TIMEOUT:-600}"

compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
instance_type="gpuspot"
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
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -o|--output-dir)              output_dir="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --eval-episodes)              eval_episodes="$2"; shift 2 ;;
    --eval-batch-size)            eval_batch_size="$2"; shift 2 ;;
    --record-video)               record_video="true"; shift ;;
    --mlflow-enable)              mlflow_enable="true"; shift ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    --from-aml-model)             from_aml_model=true; shift ;;
    --model-name)                 model_name="$2"; shift 2 ;;
    --model-version)              model_version="$2"; shift 2 ;;
    --from-blob)                  from_blob=true; shift ;;
    --storage-account)            storage_account="$2"; shift 2 ;;
    --storage-container)          storage_container="$2"; shift 2 ;;
    --blob-prefix)                blob_prefix="$2"; shift 2 ;;
    -r|--register-model)          register_model="$2"; shift 2 ;;
    --subscription-id)            subscription_id="$2"; shift 2 ;;
    --resource-group)             resource_group="$2"; shift 2 ;;
    --workspace-name)             workspace_name="$2"; shift 2 ;;
    --mlflow-token-retries)       mlflow_retries="$2"; shift 2 ;;
    --mlflow-http-timeout)        mlflow_timeout="$2"; shift 2 ;;
    --compute)                    compute="$2"; shift 2 ;;
    --instance-type)              instance_type="$2"; shift 2 ;;
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

# Policy source validation
if [[ "$from_aml_model" == "true" ]]; then
  [[ -z "$model_name" ]]    && fatal "--model-name is required with --from-aml-model"
  [[ -z "$model_version" ]] && fatal "--model-version is required with --from-aml-model"
  policy_repo_id="${model_name}:${model_version}"
elif [[ "$policy_repo_id" == *:* ]]; then
  model_name="${policy_repo_id%%:*}"
  model_version="${policy_repo_id##*:}"
  from_aml_model=true
elif [[ -z "$policy_repo_id" ]]; then
  fatal "--policy-repo-id is required (or use --from-aml-model)"
fi

# Dataset source validation
if [[ "$from_blob" == "true" ]]; then
  [[ -z "$blob_prefix" ]] && fatal "--blob-prefix is required with --from-blob"
  [[ -z "$storage_account" ]] && storage_account="$(get_storage_account)"
  [[ -z "$storage_account" ]] && fatal "--storage-account is required with --from-blob"
fi

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

case "$policy_type" in
  act|diffusion) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion)" ;;
esac

if [[ "$assets_only" != "true" ]]; then
  [[ -f "$job_file" ]] || fatal "Job file not found: $job_file"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Policy" "$policy_repo_id"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Name" "$job_name"
  print_kv "Image" "$image"
  print_kv "Eval Episodes" "$eval_episodes"
  print_kv "Eval Batch Size" "$eval_batch_size"
  print_kv "Record Video" "$record_video"
  print_kv "MLflow" "$mlflow_enable"
  print_kv "Dataset" "${dataset_repo_id:-<not set>}"
  [[ "$from_blob" == "true" ]] && print_kv "Blob Source" "$storage_account/$storage_container/$blob_prefix"
  [[ "$from_aml_model" == "true" ]] && print_kv "Model Source" "AzureML (${model_name}:${model_version})"
  print_kv "Register Model" "${register_model:-<none>}"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Compute" "${compute:-<not set>}"
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
[[ -n "$display_name" ]] && az_args+=(--set "display_name=$display_name")

# Input values
az_args+=(
  --set "inputs.policy_repo_id=$policy_repo_id"
  --set "inputs.policy_type=$policy_type"
  --set "inputs.job_name=$job_name"
  --set "inputs.output_dir=$output_dir"
  --set "inputs.eval_episodes=$eval_episodes"
  --set "inputs.eval_batch_size=$eval_batch_size"
  --set "inputs.record_video=$record_video"
  --set "inputs.mlflow_enable=$mlflow_enable"
  --set "inputs.subscription_id=$subscription_id"
  --set "inputs.resource_group=$resource_group"
  --set "inputs.workspace_name=$workspace_name"
  --set "inputs.mlflow_token_refresh_retries=$mlflow_retries"
  --set "inputs.mlflow_http_request_timeout=$mlflow_timeout"
)

[[ -n "$dataset_repo_id" ]] && az_args+=(--set "inputs.dataset_repo_id=$dataset_repo_id")
[[ -n "$lerobot_version" ]] && az_args+=(--set "inputs.lerobot_version=$lerobot_version")
[[ -n "$experiment_name" ]] && az_args+=(--set "inputs.experiment_name=$experiment_name")
[[ -n "$register_model" ]] && az_args+=(--set "inputs.register_model=$register_model")

if [[ "$from_aml_model" == "true" ]]; then
  az_args+=(--set "inputs.aml_model_name=$model_name")
  az_args+=(--set "inputs.aml_model_version=$model_version")
  az_args+=(--set "environment_variables.AML_MODEL_NAME=$model_name")
  az_args+=(--set "environment_variables.AML_MODEL_VERSION=$model_version")
fi

if [[ "$from_blob" == "true" ]]; then
  az_args+=(--set "inputs.blob_storage_account=$storage_account")
  az_args+=(--set "inputs.blob_storage_container=$storage_container")
  az_args+=(--set "inputs.blob_prefix=$blob_prefix")
  az_args+=(--set "environment_variables.BLOB_STORAGE_ACCOUNT=$storage_account")
  az_args+=(--set "environment_variables.BLOB_STORAGE_CONTAINER=$storage_container")
  az_args+=(--set "environment_variables.BLOB_PREFIX=$blob_prefix")
fi

# Environment variables (set directly — ${{inputs}} resolution is unreliable on amlcompute)
az_args+=(
  --set "environment_variables.POLICY_REPO_ID=$policy_repo_id"
  --set "environment_variables.POLICY_TYPE=$policy_type"
  --set "environment_variables.JOB_NAME=$job_name"
  --set "environment_variables.OUTPUT_DIR=$output_dir"
  --set "environment_variables.EVAL_EPISODES=$eval_episodes"
  --set "environment_variables.EVAL_BATCH_SIZE=$eval_batch_size"
  --set "environment_variables.RECORD_VIDEO=$record_video"
  --set "environment_variables.MLFLOW_ENABLE=$mlflow_enable"
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
)

[[ -n "$dataset_repo_id" ]] && az_args+=(--set "environment_variables.DATASET_REPO_ID=$dataset_repo_id")
[[ -n "$lerobot_version" ]] && az_args+=(--set "environment_variables.LEROBOT_VERSION=$lerobot_version")
[[ -n "$experiment_name" ]] && az_args+=(--set "environment_variables.EXPERIMENT_NAME=$experiment_name")
[[ -n "$register_model" ]] && az_args+=(--set "environment_variables.REGISTER_MODEL=$register_model")

[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
az_args+=(--query "name" -o "tsv")

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting AzureML LeRobot inference job..."
info "  Policy: $policy_repo_id"
info "  Policy Type: $policy_type"
info "  Job Name: $job_name"
info "  Image: $image"
info "  Eval Episodes: $eval_episodes"
[[ -n "$dataset_repo_id" ]] && info "  Dataset: $dataset_repo_id"
[[ "$from_blob" == "true" ]] && info "  Dataset: Azure Blob ($storage_account/$storage_container/$blob_prefix)"
[[ "$from_aml_model" == "true" ]] && info "  Model source: AzureML registry (${model_name}:${model_version})"
[[ "$mlflow_enable" == "true" ]] && info "  MLflow: enabled (plots logged to AzureML)"
[[ -n "$register_model" ]] && info "  Register model: $register_model"

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
print_kv "Policy" "$policy_repo_id"
print_kv "Policy Type" "$policy_type"
print_kv "Eval Episodes" "$eval_episodes"
print_kv "MLflow" "$mlflow_enable"
print_kv "Compute" "${compute:-<not set>}"
print_kv "Environment" "${environment_name}:${environment_version}"
print_kv "Workspace" "$workspace_name"
