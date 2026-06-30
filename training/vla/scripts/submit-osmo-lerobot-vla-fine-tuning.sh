#!/usr/bin/env bash
# Submit VLA fine-tuning workflow to OSMO
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
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

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: submit-osmo-lerobot-vla-fine-tuning.sh [OPTIONS] [-- osmo-submit-flags]

Submit a VLA fine-tuning workflow to OSMO.
Downloads a LeRobot dataset from Azure Blob Storage and fine-tunes a VLA (e.g Gr00t) base
model on AKS GPU nodes with optional Azure ML + ACR model upload.

REQUIRED:
        --base-model MODEL        vla base model (e.g., nvidia/GR00T-N1.5-3B)
        --data-config CONFIG      GR00T data config key (embodiment identifier)

DATA SOURCE:
        --blob-url URL            Full Azure Blob URL to LeRobot dataset
        --dataset-path PATH       Container-local dataset path (default: /data/dataset)

TRAINING HYPERPARAMETERS:
        --max-steps N             Total training iterations (default: 500)
        --batch-size N            Training batch size (default: 4)
        --save-steps N            Checkpoint save frequency (default: 100)
        --dataloader-workers N    Dataloader worker count (default: 0)

MODEL OPTIONS:
        --vla-version VER         GR00T codebase version: 1.5 or 1.7 (default: 1.5)
        --data-config-b64 B64     Base64-encoded custom data config class to inject
        --modality-config-file PATH  Python modality config (N1.7 path; auto-resolved when --vla-version 1.7)
        --modality-config-b64 B64    Base64-encoded modality config (N1.7 path)
        --embodiment-tag TAG      Embodiment tag (default: new_embodiment)
        --groot-ref REF           Isaac-GR00T git ref (auto-selected per --vla-version; override to pin)

RESUME:
        --resume                  Resume from latest checkpoint
        --run-id-override ID      Resume a specific run by ID

OUTPUT:
        --azure-upload            Mirror checkpoint to Azure ML after training
        --azureml-model-name NAME Model name for Azure ML registry (default: groot-model)
        --acr-registry NAME       Push model to ACR (e.g., acrdev001)
        --acr-model-repo PATH     ACR repository path (default: models/groot)
  HF_TOKEN env var          Optional Hugging Face token for gated model downloads (not printed)

WORKFLOW:
    -w, --workflow PATH           Workflow template (default: training/vla/workflows/osmo/groot-train.yaml)
    -j, --job-name NAME           Job identifier (default: groot-train)
    -i, --image IMAGE             Container image (default: pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel)
        --platform PLATFORM       OSMO platform name (default: h100gpu_platform)

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

EXAMPLES:
    # Basic GR00T fine-tuning with the bundled example data config
    submit-osmo-lerobot-vla-fine-tuning.sh \
      --base-model nvidia/GR00T-N1.5-3B \
      --data-config example \
      --data-config-file training/vla/configs/groot/examples/data_config.py \
      --blob-url https://myaccount.blob.core.windows.net/datasets/my-data

    # Custom embodiment auto-resolved from training/vla/configs/groot/<name>_data_config.py
    submit-osmo-lerobot-vla-fine-tuning.sh \
      --base-model nvidia/GR00T-N1.5-3B \
      --data-config my_embodiment \
      --blob-url https://myaccount.blob.core.windows.net/datasets/my-data \
      --azure-upload \
      --acr-registry acrdev001

    # Custom steps with resume
    submit-osmo-lerobot-vla-fine-tuning.sh \
      --base-model nvidia/GR00T-N1.5-3B \
      --data-config my_embodiment \
      --blob-url https://myaccount.blob.core.windows.net/datasets/my-data \
      --max-steps 2000 \
      --batch-size 8 \
      --resume --run-id-override run-20260520-174241
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

workflow=""
job_name="${JOB_NAME:-groot-train}"
image="${IMAGE:-pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel}"

blob_url="${BLOB_URL:-}"
dataset_path="${DATASET_PATH:-/data/dataset}"

vla_version="${VLA_VERSION:-1.5}"
base_model="${BASE_MODEL:-}"
data_config="${DATA_CONFIG:-}"
data_config_b64="${DATA_CONFIG_B64:-}"
data_config_file="${DATA_CONFIG_FILE:-}"
modality_config_b64="${MODALITY_CONFIG_B64:-}"
modality_config_file="${MODALITY_CONFIG_FILE:-}"
embodiment_tag="${EMBODIMENT_TAG:-new_embodiment}"
groot_ref="${ISAAC_GROOT_REF:-}"

max_steps="${MAX_STEPS:-500}"
batch_size="${BATCH_SIZE:-4}"
save_steps="${SAVE_STEPS:-100}"
dataloader_workers="${DATALOADER_WORKERS:-0}"
platform="${PLATFORM:-h100gpu_platform}"

resume="false"
run_id_override="${RUN_ID_OVERRIDE:-}"

azure_upload="false"
azureml_model_name="${AZUREML_MODEL_NAME:-groot-model}"
acr_registry="${ACR_REGISTRY:-}"
acr_model_repo="${ACR_MODEL_REPO:-models/groot}"
hf_token="${HF_TOKEN:-}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

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
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -i|--image)                   image="$2"; shift 2 ;;
    --blob-url)                   blob_url="$2"; shift 2 ;;
    --dataset-path)               dataset_path="$2"; shift 2 ;;
    --base-model)                 base_model="$2"; shift 2 ;;
    --data-config)                data_config="$2"; shift 2 ;;
    --data-config-b64)            data_config_b64="$2"; shift 2 ;;
    --data-config-file)           data_config_file="$2"; shift 2 ;;
    --modality-config-b64)        modality_config_b64="$2"; shift 2 ;;
    --modality-config-file)       modality_config_file="$2"; shift 2 ;;
    --vla-version)                vla_version="$2"; shift 2 ;;
    --embodiment-tag)             embodiment_tag="$2"; shift 2 ;;
    --groot-ref)                  groot_ref="$2"; shift 2 ;;
    --max-steps)                  max_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --save-steps)                 save_steps="$2"; shift 2 ;;
    --dataloader-workers)         dataloader_workers="$2"; shift 2 ;;
    --platform)                   platform="$2"; shift 2 ;;
    --resume)                     resume="true"; shift ;;
    --run-id-override)            run_id_override="$2"; shift 2 ;;
    --azure-upload)               azure_upload="true"; shift ;;
    --azureml-model-name)         azureml_model_name="$2"; shift 2 ;;
    --acr-registry)               acr_registry="$2"; shift 2 ;;
    --acr-model-repo)             acr_model_repo="$2"; shift 2 ;;
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

require_tools osmo

workflow="${workflow:-$REPO_ROOT/training/vla/workflows/osmo/groot-train.yaml}"

# Version-aware defaults. --vla-version selects the GR00T codebase branch and
# the matching Isaac-GR00T git ref. Modality config (N1.7 path) is auto-resolved
# from disk only for 1.7 because the N1.5 codebase consumes data config instead.
# Explicit --groot-ref / --base-model / --modality-config-* still take precedence.
case "$vla_version" in
  1.5)
    default_groot_ref="796ca8d87360913c47e9f75e17c11d63f7805048"
    default_base_model="nvidia/GR00T-N1.5-3B"
    auto_resolve_modality=false
    ;;
  1.7)
    default_groot_ref="23ace64f17aa5015259b8609d371eb61a357c776"
    default_base_model="nvidia/GR00T-N1.7-3B"
    auto_resolve_modality=true
    ;;
  *)
    fatal "Unknown --vla-version: $vla_version (must be 1.5 or 1.7)"
    ;;
esac
[[ -z "$groot_ref" ]]   && groot_ref="$default_groot_ref"
[[ -z "$base_model" ]]  && base_model="$default_base_model"

[[ -z "$base_model" ]] && fatal "--base-model is required"
[[ -z "$data_config" ]] && fatal "--data-config is required"
[[ -z "$blob_url" ]] && fatal "--blob-url is required (no dataset source configured)"
[[ -f "$workflow" ]] || fatal "Workflow template not found: $workflow"

# Auto-resolve data config Python file from training/vla/configs/groot/ when the
# caller only supplies --data-config NAME and a matching file exists on disk.
# Lookup order:
#   1. training/vla/configs/groot/${NAME}_data_config.py     (user-supplied)
#   2. training/vla/configs/groot/examples/${NAME}_config.py (bundled examples,
#      e.g. --data-config example -> examples/data_config.py)
configs_dir="$REPO_ROOT/training/vla/configs/groot"
examples_dir="$configs_dir/examples"
if [[ -z "$data_config_b64" && -z "$data_config_file" ]]; then
  for candidate in \
    "$configs_dir/${data_config}_data_config.py" \
    "$examples_dir/${data_config}_data_config.py" \
    "$examples_dir/data_config.py"; do
    if [[ "$candidate" == "$examples_dir/data_config.py" && "$data_config" != "example" ]]; then
      continue
    fi
    if [[ -f "$candidate" ]]; then
      data_config_file="$candidate"
      break
    fi
  done
fi
if [[ "$auto_resolve_modality" == "true" && -z "$modality_config_b64" && -z "$modality_config_file" ]]; then
  for candidate in \
    "$configs_dir/${data_config}_modality_config.py" \
    "$examples_dir/${data_config}_modality_config.py" \
    "$examples_dir/modality_config.py"; do
    if [[ "$candidate" == "$examples_dir/modality_config.py" && "$data_config" != "example" ]]; then
      continue
    fi
    if [[ -f "$candidate" ]]; then
      modality_config_file="$candidate"
      break
    fi
  done
fi

# Base64-encode discovered/supplied Python files (single line, no wrap).
if [[ -n "$data_config_file" && -z "$data_config_b64" ]]; then
  [[ -f "$data_config_file" ]] || fatal "Data config file not found: $data_config_file"
  data_config_b64="$(base64 < "$data_config_file" | tr -d '\n')"
fi
if [[ -n "$modality_config_file" && -z "$modality_config_b64" ]]; then
  [[ -f "$modality_config_file" ]] || fatal "Modality config file not found: $modality_config_file"
  modality_config_b64="$(base64 < "$modality_config_file" | tr -d '\n')"
fi

if [[ -z "$azureml_model_name" || "$azureml_model_name" == "groot-model" ]]; then
  azureml_model_name=$(echo "${base_model##*/}" | tr '[:upper:]' '[:lower:]')
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Job Name" "$job_name"
  print_kv "Image" "$image"
  print_kv "Workflow" "$workflow"
  print_kv "VLA Version" "$vla_version"
  print_kv "Blob URL" "$blob_url"
  print_kv "Dataset Path" "$dataset_path"
  print_kv "Base Model" "$base_model"
  print_kv "Data Config" "$data_config"
  [[ -n "$data_config_file" ]] && print_kv "Data Config File" "$data_config_file"
  [[ -n "$modality_config_file" ]] && print_kv "Modality Config File" "$modality_config_file"
  print_kv "Embodiment Tag" "$embodiment_tag"
  print_kv "Max Steps" "$max_steps"
  print_kv "Batch Size" "$batch_size"
  print_kv "Save Steps" "$save_steps"
  print_kv "Platform" "$platform"
  print_kv "Resume" "$resume"
  [[ -n "$run_id_override" ]] && print_kv "Run ID Override" "$run_id_override"
  print_kv "Azure Upload" "$azure_upload"
  [[ "$azure_upload" == "true" ]] && print_kv "Model Name" "$azureml_model_name"
  [[ -n "$acr_registry" ]] && print_kv "ACR Registry" "$acr_registry"
  [[ -n "$acr_registry" ]] && print_kv "ACR Model Repo" "$acr_model_repo"
  print_kv "Subscription" "${subscription_id:-<not set>}"
  print_kv "Resource Group" "${resource_group:-<not set>}"
  print_kv "Workspace" "${workspace_name:-<not set>}"
  exit 0
fi

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

submit_args=(
  workflow submit "$workflow"
  --set-string "image=$image"
  "workflow_name=$job_name"
  "blob_url=$blob_url"
  "dataset_path=$dataset_path"
  "batch_size=$batch_size"
  "max_steps=$max_steps"
  "save_steps=$save_steps"
  "base_model=$base_model"
  "data_config=$data_config"
  "embodiment_tag=$embodiment_tag"
  "isaac_groot_ref=$groot_ref"
  "dataloader_workers=$dataloader_workers"
  "platform=$platform"
  "resume=$resume"
  "azure_upload=$azure_upload"
  "azureml_model_name=$azureml_model_name"
)

[[ -n "$run_id_override" ]]  && submit_args+=("run_id_override=$run_id_override")
[[ -n "$data_config_b64" ]]  && submit_args+=("data_config_b64=$data_config_b64")
[[ -n "$modality_config_b64" ]] && submit_args+=("modality_config_b64=$modality_config_b64")
[[ -n "$acr_registry" ]]     && submit_args+=("acr_registry=$acr_registry")
[[ -n "$acr_registry" ]]     && submit_args+=("acr_model_repo=$acr_model_repo")
[[ -n "$subscription_id" ]]  && submit_args+=("azure_subscription_id=$subscription_id")
[[ -n "$resource_group" ]]   && submit_args+=("azureml_resource_group=$resource_group")
[[ -n "$workspace_name" ]]   && submit_args+=("azureml_workspace_name=$workspace_name")
[[ -n "$hf_token" ]]         && submit_args+=("hf_token=$hf_token")

[[ ${#forward_args[@]} -gt 0 ]] && submit_args+=("${forward_args[@]}")

#------------------------------------------------------------------------------
# Submit Workflow
#------------------------------------------------------------------------------

info "Submitting VLA fine-tuning workflow to OSMO..."
info "  Job Name: $job_name"
info "  Image: $image"
info "  Blob URL: $blob_url"
info "  Base Model: $base_model"
info "  Data Config: $data_config"
info "  Max Steps: $max_steps"
info "  Batch Size: $batch_size"
info "  Save Steps: $save_steps"
info "  Platform: $platform"
[[ "$azure_upload" == "true" ]] && info "  Azure Upload: $azureml_model_name"
[[ -n "$acr_registry" ]] && info "  ACR Push: $acr_registry.azurecr.io/$acr_model_repo"

osmo "${submit_args[@]}" || fatal "Failed to submit workflow"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Job Name" "$job_name"
print_kv "VLA Version" "$vla_version"
print_kv "Base Model" "$base_model"
print_kv "Data Config" "$data_config"
[[ -n "$data_config_file" ]] && print_kv "Data Config File" "$data_config_file"
[[ -n "$modality_config_file" ]] && print_kv "Modality Config File" "$modality_config_file"
print_kv "Max Steps" "$max_steps"
print_kv "Batch Size" "$batch_size"
print_kv "Blob URL" "$blob_url"
print_kv "Azure Upload" "$azure_upload"
[[ -n "$acr_registry" ]] && print_kv "ACR Registry" "$acr_registry"
print_kv "Workflow" "$workflow"

info "Workflow submitted successfully"
