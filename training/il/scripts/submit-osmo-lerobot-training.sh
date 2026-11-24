#!/usr/bin/env bash
# Submit LeRobot behavioral cloning training workflow to OSMO
# Supports ACT and Diffusion policy architectures with MLflow logging through the selected tracking backend
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
Usage: submit-osmo-lerobot-training.sh [OPTIONS] [-- osmo-submit-flags]

Submit a LeRobot behavioral cloning training workflow to OSMO.
Supports ACT and Diffusion policies trained from scratch and SmolVLA fine-tuned from
its pretrained base, with MLflow logging through the tracking backend of the selected
platform profile.

PLATFORM:
        --profile NAME            Platform profile: azure (default) or local (PLATFORM_PROFILE)

DATA SOURCE (mutually exclusive — provide exactly one):
    -d, --dataset-repo-id ID      HuggingFace dataset repository.
        --blob-url URL            Azure Blob dataset URL (repeatable). Uses OSMO workload identity for authentication.
        --local-dataset NAME      Dataset directory under the profile's datasets root, mounted
                                  read-only in the job (local profile).

TRAINING OPTIONS:
    -w, --workflow PATH           Workflow template (default: training/il/workflows/osmo/lerobot-train.yaml)
    -p, --policy-type TYPE        Policy architecture: act, diffusion, smolvla (default: act)
    -j, --job-name NAME           Job identifier (default: lerobot-<policy-type>-training)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/train)
    -i, --image IMAGE             Container image (default: $DEFAULT_LEROBOT_TRAIN_IMAGE, digest-pinned in scripts/lib/common.sh)
        --policy-repo-id ID       HuggingFace repo ID recorded on the trained policy (Hub push stays off)
        --lerobot-version VER     Specific LeRobot version
        --lerobot-project PATH    Repo-relative LeRobot dependency project
                                  (default: training/vla/lerobot for smolvla, else training/il/lerobot)

FINE-TUNING (smolvla defaults to the pinned lerobot/smolvla_base):
        --policy-path ID|PATH     Pretrained policy to fine-tune: HuggingFace repo or container path
        --policy-path-revision SHA  HuggingFace commit SHA that pins --policy-path (required for Hub)
        --rename-map JSON         Map dataset feature keys to the policy's, for example
                                  '{"observation.images.external": "observation.images.camera1"}'

TRAINING HYPERPARAMETERS:
        --training-steps N        Total training iterations (default: 100000)
        --batch-size N            Training batch size (default: 32)
        --learning-rate LR        Optimizer learning rate (default: 1e-4)
        --lr-warmup-steps N       Learning rate warmup steps (default: 1000)
        --eval-freq N             Evaluation frequency
        --save-freq N             Checkpoint save frequency (default: 5000)
        --log-freq N              MLflow metric log frequency (default: 200)

COMPUTE (resource defaults come from the platform profile, then these values):
        --cpu COUNT               CPU cores (default: 8)
        --memory SIZE             Memory with unit (default: 48Gi)
        --storage SIZE            Storage with unit (default: 80Gi)
        --num-gpus N              GPUs to request per task (default: 1). Sets the
                                  OSMO resources.gpu request; the training wrapper
                                  auto-detects the visible GPU count via
                                  torch.cuda.device_count() and enables Accelerate
                                  multi-GPU launch when N>1. Pair with --platform
                                  to target a node pool whose SKU exposes >= N GPUs.
        --mixed-precision MODE    Accelerate mixed-precision mode (no|fp16|bf16);
                                  default: no. Only effective when more than one
                                  GPU is visible to the job container.
        --platform NAME           OSMO platform binding the GPU node pool
                                  (default: gpu_platform, 1x A100). Use
                                  gpu_platform_2x for 2x A100 nodes with
                                  --num-gpus 2.

VALIDATION:
        --val-split RATIO         Validation split ratio (default: 0.1 = 10%%)
        --no-val-split            Disable train/val splitting

LOGGING:
        --experiment-name NAME    MLflow experiment name
        --no-system-metrics       Disable GPU/CPU/memory metrics logging

CHECKPOINT REGISTRATION:
    -r, --register-checkpoint NAME  Model name in the tracking backend's registry

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

EXAMPLES:
    # ACT training from HuggingFace Hub
    submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

    # Diffusion policy with custom learning rate
    submit-osmo-lerobot-training.sh \
      -d user/custom-dataset \
      -p diffusion \
      --learning-rate 5e-5 \
      -r my-diffusion-model

    # Fine-tune with smaller batch size
    submit-osmo-lerobot-training.sh \
      -d user/dataset \
      --policy-repo-id user/pretrained-act \
      --batch-size 16 \
      --training-steps 50000

    # Train from Azure Blob Storage
    submit-osmo-lerobot-training.sh \
      --blob-url "https://<storage-account>.blob.core.windows.net/datasets/<dataset-prefix>" \
      --no-val-split \
      -r my-act-model

    # Train on this workstation's local OSMO from a dataset in the data root
    submit-osmo-lerobot-training.sh --profile local \
      --local-dataset so101/pick-place \
      --training-steps 2000 \
      -r so101-act

    # Fine-tune SmolVLA on a local SO-101 dataset with its cameras mapped to the base model
    submit-osmo-lerobot-training.sh --profile local \
      --local-dataset so101-learner -p smolvla \
      --rename-map '{"observation.images.external": "observation.images.camera1", "observation.images.wrist": "observation.images.camera2"}' \
      --training-steps 200 --batch-size 8

    # Multi-GPU training (2 GPUs, bf16 mixed precision)
    submit-osmo-lerobot-training.sh \
      -d lerobot/aloha_sim_insertion_human \
      --num-gpus 2 \
      --platform gpu_platform_2x \
      --mixed-precision bf16
EOF
}

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

json_array() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@"
}

validate_blob_urls() {
  python3 "$REPO_ROOT/training/il/scripts/lerobot/_validate_blob_urls.py" "$@"
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

workflow="$REPO_ROOT/training/il/workflows/osmo/lerobot-train.yaml"
dataset_repo_id="${DATASET_REPO_ID:-}"
policy_type="${POLICY_TYPE:-act}"
job_name="${JOB_NAME:-}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/train}"
image="${IMAGE:-$DEFAULT_LEROBOT_TRAIN_IMAGE}"
policy_repo_id="${POLICY_REPO_ID:-}"
lerobot_version="${LEROBOT_VERSION:-}"
lerobot_project="${LEROBOT_PROJECT:-}"
policy_path="${POLICY_PATH:-}"
policy_path_revision="${POLICY_PATH_REVISION:-}"
rename_map="${RENAME_MAP:-}"
dataset_root="${DATASET_ROOT:-/workspace/data}"
local_dataset="${LOCAL_DATASET:-}"
dataset_dir=""
blob_urls=()
blob_urls_json="[]"
blob_source_count=0

training_steps="${TRAINING_STEPS:-100000}"
batch_size="${BATCH_SIZE:-32}"
learning_rate="${LEARNING_RATE:-1e-4}"
lr_warmup_steps="${LR_WARMUP_STEPS:-1000}"
eval_freq="${EVAL_FREQ:-}"
save_freq="${SAVE_FREQ:-5000}"
log_freq="${LOG_FREQ:-}"

val_split="${VAL_SPLIT:-0.1}"
val_split_enabled=true
system_metrics="${SYSTEM_METRICS:-true}"
num_gpus="${OSMO_NUM_GPUS:-1}"
mixed_precision="${MIXED_PRECISION:-no}"
platform="${OSMO_PLATFORM:-}"
cpu="${OSMO_CPU:-}"
memory="${OSMO_MEMORY:-}"
storage="${OSMO_STORAGE:-}"
profile=""

experiment_name="${EXPERIMENT_NAME:-}"
register_checkpoint="${REGISTER_CHECKPOINT:-}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
storage_account="${AZURE_STORAGE_ACCOUNT_NAME:-$(get_storage_account)}"
azure_authority_host="${AZURE_AUTHORITY_HOST:-https://login.microsoftonline.com}"
mlflow_retries="${MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES:-3}"
mlflow_timeout="${MLFLOW_HTTP_REQUEST_TIMEOUT:-60}"

payload_root="${PAYLOAD_ROOT:-/workspace/lerobot_payload}"

use_local_osmo=false
config_preview=false
forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    --profile)                    profile="$2"; shift 2 ;;
    -w|--workflow)                workflow="$2"; shift 2 ;;
    -d|--dataset|--dataset-repo-id) dataset_repo_id="$2"; shift 2 ;;
    --blob-url)                   blob_urls+=("$2"); shift 2 ;;
    --local-dataset)              local_dataset="$2"; shift 2 ;;
    -p|--policy|--policy-type)    policy_type="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -o|--output-dir)              output_dir="$2"; shift 2 ;;
    -i|--image)                   image="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --lerobot-project)            lerobot_project="$2"; shift 2 ;;
    --policy-path)                policy_path="$2"; shift 2 ;;
    --policy-path-revision)       policy_path_revision="$2"; shift 2 ;;
    --rename-map)                 rename_map="$2"; shift 2 ;;
    --steps|--training-steps)     training_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --learning-rate)              learning_rate="$2"; shift 2 ;;
    --lr-warmup-steps)            lr_warmup_steps="$2"; shift 2 ;;
    --eval-freq)                  eval_freq="$2"; shift 2 ;;
    --save-freq)                  save_freq="$2"; shift 2 ;;
    --log-freq)                   log_freq="$2"; shift 2 ;;
    --val-split)                  val_split="$2"; shift 2 ;;
    --no-val-split)               val_split_enabled=false; shift ;;
    --no-system-metrics)          system_metrics="false"; shift ;;
    --cpu)                        cpu="$2"; shift 2 ;;
    --memory)                     memory="$2"; shift 2 ;;
    --storage)                    storage="$2"; shift 2 ;;
    --num-gpus)                   num_gpus="$2"; shift 2 ;;
    --mixed-precision)            mixed_precision="$2"; shift 2 ;;
    --platform)                   platform="$2"; shift 2 ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    -r|--register-checkpoint)     register_checkpoint="$2"; shift 2 ;;
    --azure-subscription-id)      subscription_id="$2"; shift 2 ;;
    --azure-resource-group)       resource_group="$2"; shift 2 ;;
    --azure-workspace-name)       workspace_name="$2"; shift 2 ;;
    --use-local-osmo)             use_local_osmo=true; shift ;;
    --config-preview)             config_preview=true; shift ;;
    --)                           shift; forward_args=("$@"); break ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools osmo zip python3

load_platform_profile "$profile"
platform="${platform:-$PROFILE_OSMO_GPU_PLATFORM}"
cpu="${cpu:-${PROFILE_TASK_CPU:-8}}"
memory="${memory:-${PROFILE_TASK_MEMORY:-48Gi}}"
storage="${storage:-${PROFILE_TASK_STORAGE:-80Gi}}"

[[ -d "$REPO_ROOT/training/il" ]] || fatal "Directory training/il not found"
if [[ "$policy_type" == "smolvla" ]]; then
  # SmolVLA fine-tunes its pretrained base and installs from the VLA lock, which carries the smolvla extra.
  lerobot_project="${lerobot_project:-training/vla/lerobot}"
  if [[ -z "$policy_path" ]]; then
    policy_path="$DEFAULT_SMOLVLA_BASE_MODEL"
    policy_path_revision="${policy_path_revision:-$DEFAULT_SMOLVLA_BASE_REVISION}"
  fi
fi
lerobot_project="${lerobot_project:-training/il/lerobot}"
[[ -n "$policy_path" ]] && require_hf_pin "$policy_path" "$policy_path_revision" "--policy-path-revision"
if [[ -n "$rename_map" ]]; then
  python3 -c 'import json, sys; m = json.loads(sys.argv[1]); assert isinstance(m, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in m.items())' \
    "$rename_map" 2>/dev/null || fatal "--rename-map must be a JSON object of string keys to string values"
fi
case "$lerobot_project" in
  /*|../*|*/../*|*/..) fatal "--lerobot-project must be a repo-relative path without parent traversal (got: $lerobot_project)" ;;
esac
[[ -f "$REPO_ROOT/$lerobot_project/pyproject.toml" && -f "$REPO_ROOT/$lerobot_project/uv.lock" ]] \
  || fatal "LeRobot project must contain pyproject.toml and uv.lock: $lerobot_project"
entry_script="$SCRIPT_DIR/lerobot-train-osmo-entry.sh"
[[ -f "$entry_script" ]] || fatal "Entry script not found: $entry_script"
entry_script_b64="$(base64 < "$entry_script" | tr -d '\n')"

source_count=0
[[ -n "$dataset_repo_id" ]] && source_count=$((source_count + 1))
[[ ${#blob_urls[@]} -gt 0 ]] && source_count=$((source_count + 1))
[[ -n "$local_dataset" ]] && source_count=$((source_count + 1))
if [[ $source_count -gt 1 ]]; then
  fatal "--dataset-repo-id, --blob-url, and --local-dataset are mutually exclusive."
fi

if [[ "$dataset_repo_id" == azureml:* || "$dataset_repo_id" == azureml://* ]]; then
  fatal "--dataset-repo-id is for HuggingFace repositories, not AzureML data assets. Resolve the asset to a direct --blob-url for OSMO."
fi

if [[ -n "$local_dataset" ]]; then
  dataset_dir="$(platform_local_dataset_path "$local_dataset")"
  dataset_repo_id="local/${local_dataset//\//-}"
  source_mode="local"
elif [[ ${#blob_urls[@]} -gt 0 ]]; then
  dataset_repo_id="dataset"
  validate_blob_urls "${blob_urls[@]}"
  blob_urls_json=$(json_array "${blob_urls[@]}")
  blob_source_count="${#blob_urls[@]}"
  source_mode="azure-blob"
elif [[ -n "$dataset_repo_id" ]]; then
  source_mode="huggingface"
else
  fatal "No dataset source specified. Use --dataset-repo-id for HuggingFace Hub, one or more --blob-url sources, or --local-dataset."
fi

[[ -f "$workflow" ]] || fatal "Workflow template not found: $workflow"

case "$policy_type" in
  act|diffusion|smolvla) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion, smolvla)" ;;
esac
job_name="${job_name:-lerobot-${policy_type}-training}"

case "$mixed_precision" in
  no|fp16|bf16) ;;
  *) fatal "--mixed-precision must be one of: no, fp16, bf16 (got '$mixed_precision')" ;;
esac

[[ "$num_gpus" =~ ^[1-9][0-9]*$ ]] || fatal "--num-gpus must be a positive integer (got '$num_gpus')"

# AzureML tracking needs the workspace; blob datasets and Azure code staging need the storage account.
if [[ "$PROFILE_TRACKING_BACKEND" == "azureml" ]]; then
  [[ -z "$subscription_id" ]] && fatal "Azure subscription ID required (set AZURE_SUBSCRIPTION_ID or deploy infra)"
  [[ -z "$resource_group" ]] && fatal "Azure resource group required (set AZURE_RESOURCE_GROUP or deploy infra)"
  [[ -z "$workspace_name" ]] && fatal "Azure ML workspace name required (set AZUREML_WORKSPACE_NAME or deploy infra)"
fi
require_platform_code_storage
if [[ "$PROFILE_TRACKING_BACKEND" == "none" && -n "$register_checkpoint" ]]; then
  fatal "--register-checkpoint needs a tracking backend; TRACKING_BACKEND is none"
fi

[[ "$val_split_enabled" == "false" ]] && val_split="0"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_platform_profile_preview
  print_kv "Source Mode" "$source_mode"
  print_kv "Dataset" "$dataset_repo_id"
  [[ -n "$dataset_dir" ]] && print_kv "Dataset Dir" "$dataset_dir"
  print_kv "LeRobot Project" "$lerobot_project"
  [[ -n "$policy_path" ]] && print_kv "Fine-tune From" "$policy_path${policy_path_revision:+@$policy_path_revision}"
  [[ -n "$rename_map" ]] && print_kv "Rename Map" "$rename_map"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Name" "$job_name"
  print_kv "Image" "$image"
  print_kv "Output Dir" "$output_dir"
  print_kv "Dataset Root" "$dataset_root"
  print_kv "Training Steps" "$training_steps"
  print_kv "Batch Size" "$batch_size"
  print_kv "Learning Rate" "$learning_rate"
  print_kv "Save Freq" "$save_freq"
  print_kv "Val Split" "$val_split"
  print_kv "System Metrics" "$system_metrics"
  print_kv "Num GPUs" "$num_gpus"
  print_kv "CPU" "$cpu"
  print_kv "Memory" "$memory"
  print_kv "Storage" "$storage"
  print_kv "Mixed Precision" "$mixed_precision"
  print_kv "Platform" "$platform"
  [[ $blob_source_count -gt 0 ]] && print_kv "Blob URL Count" "$blob_source_count"
  print_kv "Register Model" "${register_checkpoint:-<none>}"
  if [[ "$PROFILE_TRACKING_BACKEND" == "azureml" ]]; then
    print_kv "Subscription" "$subscription_id"
    print_kv "Resource Group" "$resource_group"
    print_kv "Workspace" "$workspace_name"
  fi
  [[ "$PROFILE_NAME" == "azure" ]] && print_kv "Storage Account" "${storage_account:-<not set>}"
  print_kv "Workflow" "$workflow"
  exit 0
fi

use_platform_osmo_login
# Hub datasets and Hub policies to fine-tune use the OSMO huggingface credential when the service holds one.
hub_source=false
[[ "$source_mode" == "huggingface" ]] && hub_source=true
[[ -n "$policy_path" && "$policy_path" != /* ]] && hub_source=true
use_huggingface_credential="$(platform_huggingface_credential "$hub_source")"

#------------------------------------------------------------------------------
# Package and Upload Training Payload
#------------------------------------------------------------------------------

info "Packaging and uploading training payload..."
payload_paths=(training/il training/__init__.py training/stream.py training/utils)
[[ "$lerobot_project" == training/il/* ]] || payload_paths+=("$lerobot_project")
code_url=$(stage_and_upload_code "$REPO_ROOT" "$PROFILE_OSMO_CODE_STORAGE_URL" "${payload_paths[@]}") \
  || fatal "Failed to stage and upload training payload"
info "Training payload uploaded: $code_url"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

submit_args=(
  workflow submit "$workflow"
  --set-string "image=$image"
  --set-string "entry_script_b64=$entry_script_b64"
  "code_url=$code_url"
  "payload_root=$payload_root"
  "dataset_repo_id=$dataset_repo_id"
  "dataset_root=$dataset_root"
  "dataset_dir=$dataset_dir"
  "lerobot_project=$lerobot_project"
  "policy_path=$policy_path"
  "policy_path_revision=$policy_path_revision"
  "rename_map=$rename_map"
  "blob_urls=$blob_urls_json"
  "use_huggingface_credential=$use_huggingface_credential"
  "policy_type=$policy_type"
  "job_name=$job_name"
  "output_dir=$output_dir"
  "training_steps=$training_steps"
  "batch_size=$batch_size"
  "learning_rate=$learning_rate"
  "lr_warmup_steps=$lr_warmup_steps"
  "save_freq=$save_freq"
  "val_split=$val_split"
  "system_metrics=$system_metrics"
  "num_gpus=$num_gpus"
  "cpu=$cpu"
  "memory=$memory"
  "storage=$storage"
  "mixed_precision=$mixed_precision"
  "platform=$platform"
  "azure_authority_host=$azure_authority_host"
  "mlflow_token_refresh_retries=$mlflow_retries"
  "mlflow_http_request_timeout=$mlflow_timeout"
)

[[ -n "$policy_repo_id" ]]      && submit_args+=("policy_repo_id=$policy_repo_id")
[[ -n "$lerobot_version" ]]     && submit_args+=("lerobot_version=$lerobot_version")
[[ -n "$eval_freq" ]]           && submit_args+=("eval_freq=$eval_freq")
[[ -n "$log_freq" ]]            && submit_args+=("log_freq=$log_freq")
[[ -n "$experiment_name" ]]     && submit_args+=("experiment_name=$experiment_name")
[[ -n "$register_checkpoint" ]] && submit_args+=("register_checkpoint=$register_checkpoint")

mapfile -t tracking_values < <(platform_tracking_workflow_values)
submit_args+=("${tracking_values[@]}")

# Only AzureML tracking reads the Azure context, so other backends never receive it.
if [[ "$PROFILE_TRACKING_BACKEND" == "azureml" ]]; then
  [[ -n "$subscription_id" ]] && submit_args+=("azure_subscription_id=$subscription_id")
  [[ -n "$resource_group" ]]  && submit_args+=("azure_resource_group=$resource_group")
  [[ -n "$workspace_name" ]]  && submit_args+=("azure_workspace_name=$workspace_name")
fi

[[ ${#forward_args[@]} -gt 0 ]] && submit_args+=("${forward_args[@]}")

#------------------------------------------------------------------------------
# Submit Workflow
#------------------------------------------------------------------------------

info "Submitting LeRobot training workflow to OSMO..."
info "  Dataset: $dataset_repo_id"
info "  Policy: $policy_type"
info "  Job Name: $job_name"
info "  Image: $image"
info "  Profile: $PROFILE_NAME"
info "  Tracking: $PROFILE_TRACKING_BACKEND"
info "  Training Steps: $training_steps"
info "  Batch Size: $batch_size"
info "  Learning Rate: $learning_rate"
info "  Val Split: $val_split"
info "  System Metrics: $system_metrics"
info "  Num GPUs: $num_gpus"
info "  Mixed Precision: $mixed_precision"
info "  Platform: $platform"
info "  Code URL: $code_url"
[[ $blob_source_count -gt 0 ]] && info "  Data Source: Azure Blob URLs ($blob_source_count)"
[[ -n "$dataset_dir" ]] && info "  Data Source: local dataset at $dataset_dir"
[[ -n "$policy_path" ]] && info "  Fine-tune from: $policy_path${policy_path_revision:+@$policy_path_revision}"
[[ -n "$register_checkpoint" ]] && info "  Register model: $register_checkpoint"

osmo "${submit_args[@]}" || fatal "Failed to submit workflow"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Platform Profile" "$PROFILE_NAME"
print_kv "Tracking Backend" "$PROFILE_TRACKING_BACKEND"
print_kv "Dataset" "$dataset_repo_id"
print_kv "Source Mode" "$source_mode"
print_kv "Blob URLs" "$blob_source_count"
print_kv "Policy Type" "$policy_type"
print_kv "Job Name" "$job_name"
print_kv "Image" "$image"
print_kv "Training Steps" "$training_steps"
print_kv "Batch Size" "$batch_size"
print_kv "Learning Rate" "$learning_rate"
print_kv "Val Split" "$val_split"
print_kv "Num GPUs" "$num_gpus"
print_kv "Mixed Precision" "$mixed_precision"
print_kv "Platform" "$platform"
print_kv "Register Model" "${register_checkpoint:-<none>}"
print_kv "Workflow" "$workflow"

info "Workflow submitted successfully"
