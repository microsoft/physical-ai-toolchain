#!/usr/bin/env bash
# Submit LeRobot inference/evaluation workflow to OSMO
# Evaluates trained LeRobot policies from HuggingFace Hub
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
# shellcheck source=../../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"
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
Usage: submit-osmo-lerobot-eval.sh [OPTIONS] [-- osmo-submit-flags]

Submit a LeRobot inference/evaluation workflow to OSMO.
Replays dataset episodes through a policy from HuggingFace Hub, the tracking backend's
model registry, the Azure ML model registry, or a runnable policy carrier image, and
reports its action error.

PLATFORM:
        --profile NAME            Platform profile: azure (default) or local (PLATFORM_PROFILE)

POLICY:
    -p, --policy-type TYPE        Policy architecture: act, diffusion, smolvla (default: act)
        --rename-map JSON         Map dataset feature keys to the policy's, for example
                                  '{"observation.images.external": "observation.images.camera1"}'
        --lerobot-project PATH    Repo-relative LeRobot dependency project
                                  (default: training/vla/lerobot for smolvla, else training/il/lerobot)

POLICY SOURCE (one required):
        --policy-repo-id ID       HuggingFace policy repository (e.g., user/trained-policy)
        --policy-revision SHA      HuggingFace commit SHA to pin the policy download (required for Hub)
        --policy-uri URI          Policy registered with the tracking backend: models:/NAME/VERSION
                                  or runs:/RUN_ID/PATH
        --policy-image IMAGE      Runnable policy carrier from fleet-deployment/setup/build-model-image.sh;
                                  runs as the task image and evaluates /policy
        --from-aml-model          Load policy from AzureML model registry instead of HuggingFace
        --model-name NAME         AzureML model registry name (e.g., hve-robo-act-model)
        --model-version VERSION   AzureML model version (e.g., 4)
        --builtin-policy          Untrained baseline: a one-step mint of LeRobot's built-in act or
                                  diffusion architecture, or the pinned lerobot/smolvla_base,
                                  unadapted, for smolvla (requires --from-blob-dataset or
                                  --local-dataset)

DATASET SOURCE (one required):
    -d, --dataset-repo-id ID     HuggingFace dataset for replay evaluation
        --dataset-revision SHA    HuggingFace commit SHA to pin the dataset download (required for Hub)
        --from-blob-dataset       Download dataset from Azure Blob Storage
        --storage-account NAME    Azure storage account (default: from Terraform)
        --storage-container NAME  Blob container name (default: datasets)
        --blob-prefix PREFIX      Blob path prefix (e.g., hve-robo/hve-robo-cell)
        --local-dataset NAME      Dataset directory under the profile's datasets root, mounted
                                  read-only in the job (local profile)
    -j, --job-name NAME           Job identifier (default: lerobot-eval)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/eval)
    -i, --image IMAGE             Container image (default: $DEFAULT_LEROBOT_EVAL_IMAGE, digest-pinned in scripts/lib/common.sh)
        --lerobot-version VER     Specific LeRobot version (default: latest)
        --eval-episodes N         Number of evaluation episodes (default: 10)
        --eval-batch-size N       Evaluation batch size (default: 10)
        --record-video            Record evaluation videos

RESOURCES (defaults come from the platform profile, then these values):
        --gpu COUNT               Number of GPUs (default: 1)
        --cpu COUNT               CPU cores (default: 4)
        --memory SIZE             Memory with unit (default: 32Gi)
        --storage SIZE            Storage with unit (default: 40Gi)

MLFLOW TRACKING:
        --mlflow-enable           Log trajectory plots and metrics through the tracking backend
        --experiment-name NAME    MLflow experiment name (default: auto-derived)

MODEL REGISTRATION:
    -r, --register-model NAME     Model name for Azure ML registration (azureml tracking only)

AZURE CONTEXT:
        --azure-subscription-id ID    Azure subscription ID
        --azure-resource-group NAME   Azure resource group
        --azure-workspace-name NAME   Azure ML workspace

OTHER:
        --use-local-osmo          Use local osmo-dev CLI instead of production osmo
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

Values resolved: CLI > Environment variables > Platform profile > Terraform outputs
TRACKING_BACKEND (azureml, mlflow, or none) overrides the profile's tracking backend.
Additional arguments after -- are forwarded to osmo workflow submit.
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

workflow="$REPO_ROOT/evaluation/sil/workflows/osmo/lerobot-eval.yaml"
policy_repo_id="${POLICY_REPO_ID:-}"
policy_revision="${POLICY_REVISION:-}"
policy_type="${POLICY_TYPE:-act}"
policy_uri="${POLICY_URI:-}"
policy_image=""
rename_map="${RENAME_MAP:-}"
lerobot_project="${LEROBOT_PROJECT:-}"
dataset_repo_id="${DATASET_REPO_ID:-}"
dataset_revision="${DATASET_REVISION:-}"
job_name="${JOB_NAME:-lerobot-eval}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/eval}"
image="${IMAGE:-$DEFAULT_LEROBOT_EVAL_IMAGE}"
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
builtin_policy="${BUILTIN_POLICY:-false}"
from_blob_dataset=false
storage_account="${BLOB_STORAGE_ACCOUNT:-${AZURE_STORAGE_ACCOUNT_NAME:-}}"
storage_container="${BLOB_STORAGE_CONTAINER:-datasets}"
blob_prefix="${BLOB_PREFIX:-}"
local_dataset="${LOCAL_DATASET:-}"
dataset_dir=""
gpu="${OSMO_GPU:-}"
cpu="${OSMO_CPU:-}"
memory="${OSMO_MEMORY:-}"
storage="${OSMO_STORAGE:-}"
profile=""

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

code_storage_account="${AZURE_STORAGE_ACCOUNT_NAME:-$(get_storage_account)}"

forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    --profile)                    profile="$2"; shift 2 ;;
    -w|--workflow)                workflow="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    --policy-revision)            policy_revision="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    --policy-uri)                 policy_uri="$2"; shift 2 ;;
    --policy-image)               policy_image="$2"; shift 2 ;;
    --rename-map)                 rename_map="$2"; shift 2 ;;
    --lerobot-project)            lerobot_project="$2"; shift 2 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    --dataset-revision)           dataset_revision="$2"; shift 2 ;;
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
    --builtin-policy)             builtin_policy=true; shift ;;
    --model-name)                 model_name="$2"; shift 2 ;;
    --model-version)              model_version="$2"; shift 2 ;;
    --from-blob-dataset)          from_blob_dataset=true; shift ;;
    --storage-account)            storage_account="$2"; shift 2 ;;
    --storage-container)          storage_container="$2"; shift 2 ;;
    --blob-prefix)                blob_prefix="$2"; shift 2 ;;
    --local-dataset)              local_dataset="$2"; shift 2 ;;
    --gpu)                        gpu="$2"; shift 2 ;;
    --cpu)                        cpu="$2"; shift 2 ;;
    --memory)                     memory="$2"; shift 2 ;;
    --storage)                    storage="$2"; shift 2 ;;
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

require_tools osmo zip

load_platform_profile "$profile"
gpu="${gpu:-${PROFILE_TASK_GPU:-1}}"
cpu="${cpu:-${PROFILE_TASK_CPU:-4}}"
memory="${memory:-${PROFILE_TASK_MEMORY:-32Gi}}"
storage="${storage:-${PROFILE_TASK_STORAGE:-40Gi}}"

case "$policy_type" in
  act|diffusion|smolvla) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion, smolvla)" ;;
esac

# Policy source validation — exactly one of: --policy-image, --policy-uri, --builtin-policy,
# --from-aml-model, --policy-repo-id
other_sources=""
baseline_label=""
[[ "$builtin_policy" == "true" || "$from_aml_model" == "true" || -n "$policy_repo_id" ]] && other_sources=true
if [[ -n "$policy_image" ]]; then
  [[ -n "$other_sources" || -n "$policy_uri" ]] && fatal "--policy-image cannot be combined with another policy source"
  # A runnable carrier holds the policy at /policy and runs as the task image.
  image="$policy_image"
  policy_repo_id="/policy"
elif [[ -n "$policy_uri" ]]; then
  [[ -n "$other_sources" ]] && fatal "--policy-uri cannot be combined with another policy source"
  [[ "$policy_uri" =~ ^(models|runs):/ ]] || fatal "--policy-uri must be a models:/ or runs:/ URI (got: $policy_uri)"
  [[ "$PROFILE_TRACKING_BACKEND" != "none" ]] || fatal "--policy-uri needs a tracking backend; TRACKING_BACKEND is none"
elif [[ "$builtin_policy" == "true" && "$policy_type" == "smolvla" ]]; then
  [[ "$from_aml_model" == "true" || -n "$policy_repo_id" ]] && fatal "--builtin-policy cannot be combined with another policy source"
  # The mint trains ACT and Diffusion backbones from scratch; SmolVLA's untrained baseline is its pinned base.
  policy_repo_id="$DEFAULT_SMOLVLA_BASE_MODEL"
  policy_revision="$DEFAULT_SMOLVLA_BASE_REVISION"
  builtin_policy=false
  baseline_label="untrained baseline (pinned base, unadapted)"
elif [[ "$builtin_policy" == "true" ]]; then
  [[ "$from_aml_model" == "true" ]] && fatal "--builtin-policy cannot be combined with --from-aml-model"
  [[ -n "$policy_repo_id" ]] && fatal "--builtin-policy cannot be combined with --policy-repo-id"
elif [[ "$from_aml_model" == "true" ]]; then
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
  [[ -z "$policy_repo_id" ]] && fatal "A policy source is required: use --builtin-policy, --policy-repo-id, or --from-aml-model"
  require_hf_pin "$policy_repo_id" "$policy_revision" "--policy-revision"
fi

# The built-in mint trains a single step from a dataset on disk: a Blob download or a local dataset.
if [[ "$builtin_policy" == "true" && "$from_blob_dataset" != "true" && -z "$local_dataset" ]]; then
  fatal "--builtin-policy requires --from-blob-dataset or --local-dataset (the base policy is minted from the dataset)"
fi

# Dataset source validation
if [[ -n "$local_dataset" ]]; then
  [[ "$from_blob_dataset" == "true" || -n "$dataset_repo_id" ]] \
    && fatal "--local-dataset cannot be combined with --from-blob-dataset or --dataset-repo-id"
  dataset_dir="$(platform_local_dataset_path "$local_dataset")"
elif [[ "$from_blob_dataset" == "true" ]]; then
  [[ -z "$blob_prefix" ]] && fatal "--blob-prefix is required with --from-blob-dataset"
  [[ -z "$storage_account" ]] && storage_account="$(get_storage_account)"
  [[ -z "$storage_account" ]] && fatal "--storage-account is required with --from-blob-dataset"
else
  [[ -z "$dataset_repo_id" ]] && fatal "--dataset-repo-id is required (or use --from-blob-dataset)"
  [[ -z "$dataset_revision" ]] && fatal "--dataset-revision is required with --dataset-repo-id"
fi

[[ -f "$workflow" ]] || fatal "Workflow template not found: $workflow"
[[ -d "$REPO_ROOT/training/il" ]] || fatal "Directory training/il not found"

if [[ "$policy_type" == "smolvla" ]]; then
  lerobot_project="${lerobot_project:-training/vla/lerobot}"
fi
lerobot_project="${lerobot_project:-training/il/lerobot}"
case "$lerobot_project" in
  /*|../*|*/../*|*/..) fatal "--lerobot-project must be a repo-relative path without parent traversal (got: $lerobot_project)" ;;
esac
[[ -f "$REPO_ROOT/$lerobot_project/pyproject.toml" && -f "$REPO_ROOT/$lerobot_project/uv.lock" ]] \
  || fatal "LeRobot project must contain pyproject.toml and uv.lock: $lerobot_project"
if [[ -n "$rename_map" ]]; then
  python3 -c 'import json, sys; m = json.loads(sys.argv[1]); assert isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in m.items())' \
    "$rename_map" 2>/dev/null || fatal "--rename-map must be a JSON object of string keys to string values"
fi

# Hub downloads use the OSMO huggingface credential when the service holds one (checked at submission).
hub_source=false
[[ -n "$dataset_repo_id" ]] && hub_source=true
[[ -n "$policy_repo_id" && "$policy_repo_id" != /* && "$from_aml_model" != "true" ]] && hub_source=true

# The AzureML registry, AzureML tracking, and policies downloaded through AzureML tracking need the workspace;
# other tracking backends do not.
needs_workspace=false
[[ "$from_aml_model" == "true" ]] && needs_workspace=true
[[ -n "$register_model" && "$PROFILE_TRACKING_BACKEND" == "azureml" ]] && needs_workspace=true
[[ "$mlflow_enable" == "true" && "$PROFILE_TRACKING_BACKEND" == "azureml" ]] && needs_workspace=true
[[ -n "$policy_uri" && "$PROFILE_TRACKING_BACKEND" == "azureml" ]] && needs_workspace=true
if [[ "$needs_workspace" == "true" ]]; then
  [[ -z "$subscription_id" ]] && fatal "Azure subscription ID required for the model registry, MLflow, or --policy-uri"
  [[ -z "$resource_group" ]] && fatal "Azure resource group required for the model registry, MLflow, or --policy-uri"
  [[ -z "$workspace_name" ]] && fatal "Azure ML workspace name required for the model registry, MLflow, or --policy-uri"
fi
[[ -n "$register_model" && "$PROFILE_TRACKING_BACKEND" != "azureml" ]] \
  && fatal "--register-model registers to Azure ML; it needs TRACKING_BACKEND=azureml"
require_platform_code_storage

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_platform_profile_preview
  print_kv "OSMO Platform" "$PROFILE_OSMO_GPU_PLATFORM"
  print_kv "Policy" "${policy_uri:-$policy_repo_id}${policy_revision:+@$policy_revision}"
  print_kv "Policy Type" "$policy_type"
  [[ "$builtin_policy" == "true" ]] && baseline_label="untrained baseline (one-step mint)"
  [[ -n "$baseline_label" ]] && print_kv "Policy Source" "$baseline_label"
  [[ -n "$policy_image" ]] && print_kv "Policy Image" "$policy_image (task image)"
  [[ -n "$rename_map" ]] && print_kv "Rename Map" "$rename_map"
  print_kv "LeRobot Project" "$lerobot_project"
  print_kv "Job Name" "$job_name"
  print_kv "Image" "$image"
  print_kv "Eval Episodes" "$eval_episodes"
  print_kv "Eval Batch Size" "$eval_batch_size"
  print_kv "Record Video" "$record_video"
  print_kv "MLflow" "$mlflow_enable"
  [[ -n "$dataset_repo_id" ]] && print_kv "Dataset" "$dataset_repo_id"
  [[ "$from_blob_dataset" == "true" ]] && print_kv "Blob Source" "$storage_account/$storage_container/$blob_prefix"
  [[ -n "$dataset_dir" ]] && print_kv "Dataset Dir" "$dataset_dir"
  [[ "$from_aml_model" == "true" ]] && print_kv "Model Source" "AzureML (${model_name}:${model_version})"
  print_kv "Register Model" "${register_model:-<none>}"
  print_kv "GPU" "$gpu"
  print_kv "CPU" "$cpu"
  print_kv "Memory" "$memory"
  print_kv "Storage" "$storage"
  if [[ "$needs_workspace" == "true" ]]; then
    print_kv "Subscription" "${subscription_id:-<not set>}"
    print_kv "Resource Group" "${resource_group:-<not set>}"
    print_kv "Workspace" "${workspace_name:-<not set>}"
  fi
  [[ "$PROFILE_NAME" == "azure" ]] && print_kv "Storage Account" "${code_storage_account:-<not set>}"
  print_kv "Workflow" "$workflow"
  exit 0
fi

use_platform_osmo_login
use_huggingface_credential="$(platform_huggingface_credential "$hub_source")"

#------------------------------------------------------------------------------
# Package and Upload Runtime Payload
#------------------------------------------------------------------------------

payload_root="${PAYLOAD_ROOT:-/workspace/lerobot_payload}"

# Base64-encode the OSMO entry script (a lintable repo file) so the workflow
# YAML stays script-free; OSMO writes it as a *.b64 file the task bootstrap
# decodes. The evaluation Python modules travel inside the code archive below.
entry_script="$SCRIPT_DIR/osmo-lerobot-eval-entry.sh"
[[ -f "$entry_script" ]] || fatal "Entry script not found: $entry_script"
entry_script_b64="$(base64 < "$entry_script" | tr -d '\n')"

info "Packaging and uploading LeRobot runtime payload..."
payload_paths=(training/__init__.py training/il training/utils evaluation/sil evaluation/metrics workflows/azureml)
[[ "$lerobot_project" == training/il/* ]] || payload_paths+=("$lerobot_project")
code_url=$(stage_and_upload_code "$REPO_ROOT" "$PROFILE_OSMO_CODE_STORAGE_URL" "${payload_paths[@]}") \
  || fatal "Failed to stage and upload runtime payload"
info "Runtime payload uploaded: $code_url"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

submit_args=(
  workflow submit "$workflow"
  --set-string "image=$image"
  "code_url=$code_url"
  "entry_script_b64=$entry_script_b64"
  "payload_root=$payload_root"
  "policy_repo_id=$policy_repo_id"
  "policy_type=$policy_type"
  "policy_uri=$policy_uri"
  "rename_map=$rename_map"
  "lerobot_project=$lerobot_project"
  "job_name=$job_name"
  "output_dir=$output_dir"
  "eval_episodes=$eval_episodes"
  "eval_batch_size=$eval_batch_size"
  "record_video=$record_video"
  "use_huggingface_credential=$use_huggingface_credential"
  "dataset_dir=$dataset_dir"
  "gpu=$gpu"
  "cpu=$cpu"
  "memory=$memory"
  "storage=$storage"
  "platform=$PROFILE_OSMO_GPU_PLATFORM"
)
mapfile -t tracking_values < <(platform_tracking_workflow_values)
submit_args+=("${tracking_values[@]}")

[[ -n "$policy_revision" ]] && submit_args+=("policy_revision=$policy_revision")
[[ -n "$dataset_repo_id" ]]  && submit_args+=("dataset_repo_id=$dataset_repo_id")
[[ -n "$dataset_revision" ]] && submit_args+=("dataset_revision=$dataset_revision")
[[ -n "$lerobot_version" ]]  && submit_args+=("lerobot_version=$lerobot_version")
[[ "$mlflow_enable" == "true" ]] && submit_args+=("mlflow_enable=true")
[[ -n "$experiment_name" ]]  && submit_args+=("experiment_name=$experiment_name")
[[ -n "$register_model" ]]   && submit_args+=("register_model=$register_model")

# AzureML model registry source
if [[ "$from_aml_model" == "true" ]]; then
  submit_args+=("aml_model_name=$model_name" "aml_model_version=$model_version")
fi

# Built-in base policy mint
[[ "$builtin_policy" == "true" ]] && submit_args+=("builtin_policy=true")

# Azure Blob dataset source
if [[ "$from_blob_dataset" == "true" ]]; then
  submit_args+=("blob_storage_account=$storage_account" "blob_storage_container=$storage_container" "blob_prefix=$blob_prefix")
fi

if [[ "$needs_workspace" == "true" ]]; then
  [[ -n "$subscription_id" ]] && submit_args+=("azure_subscription_id=$subscription_id")
  [[ -n "$resource_group" ]]  && submit_args+=("azure_resource_group=$resource_group")
  [[ -n "$workspace_name" ]]  && submit_args+=("azure_workspace_name=$workspace_name")
fi

[[ ${#forward_args[@]} -gt 0 ]] && submit_args+=("${forward_args[@]}")

#------------------------------------------------------------------------------
# Submit Workflow
#------------------------------------------------------------------------------

info "Submitting LeRobot inference workflow to OSMO..."
info "  Policy: ${policy_uri:-$policy_repo_id}"
info "  Policy Type: $policy_type"
[[ -n "$policy_image" ]] && info "  Policy image: $policy_image"
info "  Job Name: $job_name"
info "  Eval Episodes: $eval_episodes"
info "  Image: $image"
[[ "$builtin_policy" == "true" ]] && info "  Policy source: LeRobot built-in (minted base policy)"
[[ -n "$dataset_repo_id" ]] && info "  Dataset: $dataset_repo_id"
[[ "$from_blob_dataset" == "true" ]] && info "  Dataset: Azure Blob ($storage_account/$storage_container/$blob_prefix)"
[[ -n "$dataset_dir" ]] && info "  Dataset: local ($dataset_dir)"
[[ "$from_aml_model" == "true" ]] && info "  Model source: AzureML registry (${model_name}:${model_version})"
[[ "$mlflow_enable" == "true" ]] && info "  MLflow: enabled (${PROFILE_TRACKING_BACKEND} tracking backend)"
[[ -n "$experiment_name" ]] && info "  Experiment: $experiment_name"
[[ -n "$register_model" ]] && info "  Register model: $register_model"

osmo "${submit_args[@]}" || fatal "Failed to submit workflow"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Platform Profile" "$PROFILE_NAME"
print_kv "Tracking Backend" "$PROFILE_TRACKING_BACKEND"
print_kv "Policy" "${policy_uri:-$policy_repo_id}"
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
