#!/usr/bin/env bash
# Submit LeRobot inference/evaluation workflow to OSMO
# Evaluates trained LeRobot policies from HuggingFace Hub
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

source "$REPO_ROOT/shared/lib/common.sh"
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
Usage: submit-osmo-lerobot-inference.sh [OPTIONS] [-- osmo-submit-flags]

Submit a LeRobot inference/evaluation workflow to OSMO.
Evaluates trained policies from HuggingFace Hub or Azure ML model registry.

POLICY SOURCE (one required):
        --policy-repo-id ID       HuggingFace policy repository (e.g., user/trained-policy)
        --from-aml-model          Load policy from AzureML model registry instead of HuggingFace
        --model-name NAME         AzureML model registry name (e.g., hve-robo-act-model)
        --model-version VERSION   AzureML model version (e.g., 4)

DATASET SOURCE (one required):
    -d, --dataset-repo-id ID     HuggingFace dataset for replay evaluation
        --from-blob-dataset       Download dataset from Azure Blob Storage
        --storage-account NAME    Azure storage account (default: from Terraform)
        --storage-container NAME  Blob container name (default: datasets)
        --blob-prefix PREFIX      Blob path prefix (e.g., hve-robo/hve-robo-cell)
    -j, --job-name NAME           Job identifier (default: lerobot-eval)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/eval)
    -i, --image IMAGE             Container image (default: pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime)
        --lerobot-version VER     Specific LeRobot version (default: latest)
        --eval-episodes N         Number of evaluation episodes (default: 10)
        --eval-batch-size N       Evaluation batch size (default: 10)
        --record-video            Record evaluation videos

MLFLOW TRACKING:
        --mlflow-enable           Enable MLflow logging with trajectory plots to AzureML
        --experiment-name NAME    MLflow experiment name (default: auto-derived)

MODEL REGISTRATION:
    -r, --register-model NAME     Model name for Azure ML registration

AZURE CONTEXT:
        --azure-subscription-id ID    Azure subscription ID
        --azure-resource-group NAME   Azure resource group
        --azure-workspace-name NAME   Azure ML workspace

OTHER:
        --use-local-osmo          Use local osmo-dev CLI instead of production osmo
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

Values resolved: CLI > Environment variables > Terraform outputs
Additional arguments after -- are forwarded to osmo workflow submit.
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

workflow="$REPO_ROOT/evaluation/sil/workflows/osmo/lerobot-eval.yaml"
policy_repo_id="${POLICY_REPO_ID:-}"
policy_type="${POLICY_TYPE:-act}"
dataset_repo_id="${DATASET_REPO_ID:-}"
job_name="${JOB_NAME:-lerobot-eval}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/eval}"
image="${IMAGE:-pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime}"
lerobot_version="${LEROBOT_VERSION:-}"

eval_episodes="${EVAL_EPISODES:-10}"
eval_batch_size="${EVAL_BATCH_SIZE:-10}"
record_video="${RECORD_VIDEO:-false}"
mlflow_enable="${MLFLOW_ENABLE:-false}"
experiment_name="${EXPERIMENT_NAME:-}"
register_model="${REGISTER_MODEL:-}"
use_local_osmo=false
config_preview=false

from_aml_model=false
model_name="${AML_MODEL_NAME:-}"
model_version="${AML_MODEL_VERSION:-}"
from_blob_dataset=false
storage_account="${BLOB_STORAGE_ACCOUNT:-${AZURE_STORAGE_ACCOUNT_NAME:-}}"
storage_container="${BLOB_STORAGE_CONTAINER:-datasets}"
blob_prefix="${BLOB_PREFIX:-}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    -w|--workflow)                workflow="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -o|--output-dir)              output_dir="$2"; shift 2 ;;
    -i|--image)                   image="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --eval-episodes)              eval_episodes="$2"; shift 2 ;;
    --eval-batch-size)            eval_batch_size="$2"; shift 2 ;;
    --record-video)               record_video="true"; shift ;;
    --mlflow-enable)              mlflow_enable="true"; shift ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    --from-aml-model)             from_aml_model=true; shift ;;
    --model-name)                 model_name="$2"; shift 2 ;;
    --model-version)              model_version="$2"; shift 2 ;;
    --from-blob-dataset)          from_blob_dataset=true; shift ;;
    --storage-account)            storage_account="$2"; shift 2 ;;
    --storage-container)          storage_container="$2"; shift 2 ;;
    --blob-prefix)                blob_prefix="$2"; shift 2 ;;
    -r|--register-model)          register_model="$2"; shift 2 ;;
    --azure-subscription-id)      subscription_id="$2"; shift 2 ;;
    --azure-resource-group)       resource_group="$2"; shift 2 ;;
    --azure-workspace-name)       workspace_name="$2"; shift 2 ;;
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

require_tools osmo
require_tools osmo zip base64

# Policy source validation
if [[ "$from_aml_model" == "true" ]]; then
  [[ -z "$model_name" ]]    && fatal "--model-name is required with --from-aml-model"
  [[ -z "$model_version" ]] && fatal "--model-version is required with --from-aml-model"
  policy_repo_id="${model_name}:${model_version}"
elif [[ "$policy_repo_id" == *:* ]]; then
  # Auto-detect AzureML model registry format (name:version)
  model_name="${policy_repo_id%%:*}"
  model_version="${policy_repo_id##*:}"
  from_aml_model=true
  info "Auto-detected AzureML model: ${model_name} version ${model_version}"
else
  [[ -z "$policy_repo_id" ]] && fatal "--policy-repo-id is required (or use --from-aml-model)"
fi

# Dataset source validation
if [[ "$from_blob_dataset" == "true" ]]; then
  [[ -z "$blob_prefix" ]] && fatal "--blob-prefix is required with --from-blob-dataset"
  [[ -z "$storage_account" ]] && storage_account="$(get_storage_account)"
  [[ -z "$storage_account" ]] && fatal "--storage-account is required with --from-blob-dataset"
else
  [[ -z "$dataset_repo_id" ]] && fatal "--dataset-repo-id is required (or use --from-blob-dataset)"
fi

[[ -f "$workflow" ]] || fatal "Workflow template not found: $workflow"
[[ -d "$REPO_ROOT/training/il" ]] || fatal "Directory training/il not found"

case "$policy_type" in
  act|diffusion) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion)" ;;
esac

if [[ -n "$register_model" || "$mlflow_enable" == "true" || "$from_aml_model" == "true" ]]; then
  [[ -z "$subscription_id" ]] && fatal "Azure subscription ID required for model registry / MLflow"
  [[ -z "$resource_group" ]] && fatal "Azure resource group required for model registry / MLflow"
  [[ -z "$workspace_name" ]] && fatal "Azure ML workspace name required for model registry / MLflow"
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
  [[ -n "$dataset_repo_id" ]] && print_kv "Dataset" "$dataset_repo_id"
  [[ "$from_blob_dataset" == "true" ]] && print_kv "Blob Source" "$storage_account/$storage_container/$blob_prefix"
  [[ "$from_aml_model" == "true" ]] && print_kv "Model Source" "AzureML (${model_name}:${model_version})"
  print_kv "Register Model" "${register_model:-<none>}"
  print_kv "Subscription" "${subscription_id:-<not set>}"
  print_kv "Resource Group" "${resource_group:-<not set>}"
  print_kv "Workspace" "${workspace_name:-<not set>}"
  print_kv "Workflow" "$workflow"
  exit 0
fi

#------------------------------------------------------------------------------
# Package Runtime Payload
#------------------------------------------------------------------------------

TMP_DIR="$SCRIPT_DIR/.tmp"
ARCHIVE_PATH="$TMP_DIR/osmo-lerobot-inference.zip"
B64_PATH="$TMP_DIR/osmo-lerobot-inference.b64"
payload_root="${PAYLOAD_ROOT:-/workspace/lerobot_payload}"

info "Packaging LeRobot runtime payload..."
mkdir -p "$TMP_DIR"
rm -f "$ARCHIVE_PATH" "$B64_PATH"

(cd "$REPO_ROOT" && zip -qr "$ARCHIVE_PATH" training/il evaluation/sil \
  -x "**/__pycache__/*" \
  -x "*.pyc" \
  -x "*.pyo" \
  -x "**/.pytest_cache/*" \
  -x "**/.mypy_cache/*" \
  -x "**/*.egg-info/*" \
  -x "**/.tmp/*" \
  -x "**/*.sh" \
  -x "**/workflows/*") || fatal "Failed to create runtime archive"

[[ -f "$ARCHIVE_PATH" ]] || fatal "Archive not created: $ARCHIVE_PATH"

if base64 --help 2>&1 | grep -q '\-\-input'; then
  base64 --input "$ARCHIVE_PATH" | tr -d '\n' > "$B64_PATH"
else
  base64 -i "$ARCHIVE_PATH" | tr -d '\n' > "$B64_PATH"
fi

[[ -s "$B64_PATH" ]] || fatal "Failed to encode archive"
encoded_payload=$(<"$B64_PATH")

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

submit_args=(
  workflow submit "$workflow"
  --set-string "image=$image"
  "encoded_archive=$encoded_payload"
  "payload_root=$payload_root"
  "policy_repo_id=$policy_repo_id"
  "policy_type=$policy_type"
  "job_name=$job_name"
  "output_dir=$output_dir"
  "eval_episodes=$eval_episodes"
  "eval_batch_size=$eval_batch_size"
  "record_video=$record_video"
)

[[ -n "$dataset_repo_id" ]]  && submit_args+=("dataset_repo_id=$dataset_repo_id")
[[ -n "$lerobot_version" ]]  && submit_args+=("lerobot_version=$lerobot_version")
[[ "$mlflow_enable" == "true" ]] && submit_args+=("mlflow_enable=true")
[[ -n "$experiment_name" ]]  && submit_args+=("experiment_name=$experiment_name")
[[ -n "$register_model" ]]   && submit_args+=("register_model=$register_model")

# AzureML model registry source
if [[ "$from_aml_model" == "true" ]]; then
  submit_args+=("aml_model_name=$model_name" "aml_model_version=$model_version")
fi

# Azure Blob dataset source
if [[ "$from_blob_dataset" == "true" ]]; then
  submit_args+=("blob_storage_account=$storage_account" "blob_storage_container=$storage_container" "blob_prefix=$blob_prefix")
fi

[[ -n "$subscription_id" ]] && submit_args+=("azure_subscription_id=$subscription_id")
[[ -n "$resource_group" ]]  && submit_args+=("azure_resource_group=$resource_group")
[[ -n "$workspace_name" ]]  && submit_args+=("azure_workspace_name=$workspace_name")

[[ ${#forward_args[@]} -gt 0 ]] && submit_args+=("${forward_args[@]}")

#------------------------------------------------------------------------------
# Submit Workflow
#------------------------------------------------------------------------------

info "Submitting LeRobot inference workflow to OSMO..."
info "  Policy: $policy_repo_id"
info "  Policy Type: $policy_type"
info "  Job Name: $job_name"
info "  Eval Episodes: $eval_episodes"
info "  Image: $image"
[[ -n "$dataset_repo_id" ]] && info "  Dataset: $dataset_repo_id"
[[ "$from_blob_dataset" == "true" ]] && info "  Dataset: Azure Blob ($storage_account/$storage_container/$blob_prefix)"
[[ "$from_aml_model" == "true" ]] && info "  Model source: AzureML registry (${model_name}:${model_version})"
[[ "$mlflow_enable" == "true" ]] && info "  MLflow: enabled (plots logged to AzureML)"
[[ -n "$experiment_name" ]] && info "  Experiment: $experiment_name"
[[ -n "$register_model" ]] && info "  Register model: $register_model"

osmo "${submit_args[@]}" || fatal "Failed to submit workflow"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Policy" "$policy_repo_id"
print_kv "Policy Type" "$policy_type"
print_kv "Job Name" "$job_name"
print_kv "Image" "$image"
print_kv "Eval Episodes" "$eval_episodes"
print_kv "MLflow" "$mlflow_enable"
[[ -n "$dataset_repo_id" ]] && print_kv "Dataset" "$dataset_repo_id"
[[ "$from_aml_model" == "true" ]] && print_kv "Model Source" "AzureML (${model_name}:${model_version})"
print_kv "Register Model" "${register_model:-<none>}"
print_kv "Workflow" "$workflow"

info "Workflow submitted successfully"
