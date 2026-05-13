#!/usr/bin/env bash
# Submit OpenVLA-OFT fine-tuning to Azure ML.
#
# Mirrors submit-azureml-lerobot-training.sh's resolution / submission patterns
# (Terraform outputs > env > CLI flags, idempotent environment registration,
# az ml job create with --set overrides) but targets a different entry script
# and job YAML purpose-built for the OFT recipe.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

show_help() {
  cat <<'EOF'
Usage: submit-azureml-openvla-oft-training.sh [OPTIONS] [-- az-ml-job-flags]

Submit OpenVLA-OFT fine-tuning to Azure ML.

DATA SOURCE:
    -d, --dataset-repo-id ID     Logical name (matches blob folder)
        --blob-url URL           Add blob dataset URL (repeatable; merged at runtime)
        --dataset-asset SPEC     AzureML data asset (NAME:VERSION or NAME@latest). Mounts the asset
                                 read-only at $DATASET_MOUNT; takes precedence over --blob-url.
        --dataset-root DIR       Container mount path (default: /workspace/data)
        --dataset-name NAME      RLDS dataset name (default: schaeffler_bimanual)

PROFILES:
        --profile NAME           Apply preset (overrides individual flags below where unset):
                                   dryrun-a10  - A10 24 GB smoke test (1000 steps, batch=1, 1 image)
                                   prod-a100   - 2x A100 80GB full OFT recipe (default)

JOB IDENTITY:
    -j, --job-name NAME          Job identifier (default: openvla-oft-training)
        --display-name NAME      Display name override
        --experiment-name NAME   Experiment name override

OFT RECIPE:
        --vla-path PATH          Base VLA HF id (default: openvla/openvla-7b)
        --openvla-oft-ref REF    moojink/openvla-oft git ref (default: main)
        --transformers-ref REF   moojink/transformers-openvla-oft ref (default: main)
        --image-key-primary K    Primary camera feature key
        --image-key-left K       Left wrist camera feature key
        --image-key-right K      Right wrist camera feature key
        --action-dim N           Action vector dim (default: 12)
        --proprio-dim N          Proprio vector dim (default: 12)
        --num-actions-chunk N    Action chunk length (default: 25)
        --num-images N           Images in input (default: 3)
        --use-film BOOL          Enable FiLM (default: True)
        --use-proprio BOOL       Enable proprio (default: True)
        --use-l1-regression BOOL Enable L1 regression head (default: True)

HYPERPARAMETERS:
        --batch-size N           Per-device batch (default: 4)
        --learning-rate F        Learning rate (default: 5e-4)
        --num-steps-before-decay N (default: 50000)
        --max-steps N            (default: 100005)
        --save-freq N            (default: 10000)
        --lora-rank N            (default: 32)
        --image-aug BOOL         (default: True)
        --num-gpus N             nproc-per-node for torchrun (default: 2)

AZUREML ASSETS:
        --environment-name NAME  AzureML env name (default: openvla-oft-training-env)
        --environment-version V  Env version (default: 1.0.0)
        --image IMAGE            Container image (default: pytorch/pytorch:2.2.0-cuda12.1-cudnn8-runtime)
        --assets-only            Register environment without submitting job

AZURE CONTEXT:
        --subscription-id ID
        --resource-group NAME
        --workspace-name NAME
        --compute TARGET
        --instance-type NAME     Instance type (default: gpu-a100)
        --stream                 Stream logs after submission
    -a, --save-as PATH

GENERAL:
    -h, --help
        --config-preview         Print configuration and exit

Resolution order: CLI > env vars > Terraform outputs.
EOF
}

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

# -------- Defaults --------
environment_name="openvla-oft-training-env"
environment_version="1.0.0"
image="${IMAGE:-pytorch/pytorch:2.2.0-cuda12.1-cudnn8-runtime}"
assets_only=false

job_file="$REPO_ROOT/training/il/workflows/azureml/openvla-oft-train.yaml"
dataset_repo_id="${DATASET_REPO_ID:-}"
dataset_name="${DATASET_NAME:-schaeffler_bimanual}"
job_name="${JOB_NAME:-openvla-oft-training}"
display_name=""
experiment_name=""

vla_path="${VLA_PATH:-openvla/openvla-7b}"
openvla_oft_ref="${OPENVLA_OFT_REF:-main}"
transformers_ref="${TRANSFORMERS_FORK_REF:-main}"
image_key_primary="${IMAGE_KEY_PRIMARY:-observation.images.d405_stationary_r_0}"
image_key_left="${IMAGE_KEY_LEFT_WRIST:-observation.images.d405_stationary_l_1}"
image_key_right="${IMAGE_KEY_RIGHT_WRIST:-observation.images.d405_stationary_l_2}"
action_dim="${ACTION_DIM:-12}"
proprio_dim="${PROPRIO_DIM:-12}"
num_actions_chunk="${NUM_ACTIONS_CHUNK:-25}"
num_images_in_input="${NUM_IMAGES_IN_INPUT:-3}"
use_film="${USE_FILM:-True}"
use_proprio="${USE_PROPRIO:-True}"
use_l1_regression="${USE_L1_REGRESSION:-True}"
batch_size="${BATCH_SIZE:-4}"
learning_rate="${LEARNING_RATE:-5e-4}"
num_steps_before_decay="${NUM_STEPS_BEFORE_DECAY:-50000}"
max_steps="${MAX_STEPS:-100005}"
save_freq="${SAVE_FREQ:-10000}"
lora_rank="${LORA_RANK:-32}"
image_aug="${IMAGE_AUG:-True}"
num_gpus="${NUM_GPUS:-2}"

blob_urls=()
dataset_root="${DATASET_ROOT:-/workspace/data}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
instance_type="gpu-a100"
stream_logs=false
save_as=""
config_preview=false
forward_args=()
dataset_asset="${DATASET_ASSET:-}"
profile=""

# --- Profiles (applied lazily after parse) ---
apply_profile() {
  case "$1" in
    dryrun-a10)
      # A10 24 GB smoke test: validates the pipeline (clone -> RLDS -> torchrun ->
      # checkpoint upload) with minimum memory footprint. Drops to single image,
      # no FiLM/proprio, lora_rank=16, action_chunk=8, batch=1.
      [[ -z "${PROFILE_BATCH_SIZE_SET:-}" ]]           && batch_size=1
      [[ -z "${PROFILE_NUM_IMAGES_SET:-}" ]]           && num_images_in_input=1
      [[ -z "${PROFILE_USE_FILM_SET:-}" ]]             && use_film=False
      [[ -z "${PROFILE_USE_PROPRIO_SET:-}" ]]          && use_proprio=False
      [[ -z "${PROFILE_NUM_ACTIONS_CHUNK_SET:-}" ]]    && num_actions_chunk=8
      [[ -z "${PROFILE_LORA_RANK_SET:-}" ]]            && lora_rank=16
      [[ -z "${PROFILE_IMAGE_AUG_SET:-}" ]]            && image_aug=False
      [[ -z "${PROFILE_MAX_STEPS_SET:-}" ]]            && max_steps=1000
      [[ -z "${PROFILE_SAVE_FREQ_SET:-}" ]]            && save_freq=500
      [[ -z "${PROFILE_NUM_STEPS_BEFORE_DECAY_SET:-}" ]] && num_steps_before_decay=800
      [[ -z "${PROFILE_NUM_GPUS_SET:-}" ]]             && num_gpus=1
      [[ -z "${PROFILE_INSTANCE_TYPE_SET:-}" ]]        && instance_type=gpu
      [[ -z "${PROFILE_JOB_NAME_SET:-}" ]]             && job_name=openvla-oft-dryrun-a10
      ;;
    prod-a100|"")
      : # defaults already match the OFT+ ALOHA recipe targeting 2x A100 80GB
      ;;
    *)
      fatal "Unknown profile: $1 (expected dryrun-a10 or prod-a100)"
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                  show_help; exit 0 ;;
    --environment-name)         environment_name="$2"; shift 2 ;;
    --environment-version)      environment_version="$2"; shift 2 ;;
    --image|-i)                 image="$2"; shift 2 ;;
    --assets-only)              assets_only=true; shift ;;
    --profile)                  profile="$2"; shift 2 ;;
    -d|--dataset-repo-id)       dataset_repo_id="$2"; shift 2 ;;
    --dataset-name)             dataset_name="$2"; shift 2 ;;
    --dataset-asset)            dataset_asset="$2"; shift 2 ;;
    --blob-url)                 blob_urls+=("$2"); shift 2 ;;
    --dataset-root)             dataset_root="$2"; shift 2 ;;
    -j|--job-name)              job_name="$2"; PROFILE_JOB_NAME_SET=1; shift 2 ;;
    --display-name)             display_name="$2"; shift 2 ;;
    --experiment-name)          experiment_name="$2"; shift 2 ;;
    --vla-path)                 vla_path="$2"; shift 2 ;;
    --openvla-oft-ref)          openvla_oft_ref="$2"; shift 2 ;;
    --transformers-ref)         transformers_ref="$2"; shift 2 ;;
    --image-key-primary)        image_key_primary="$2"; shift 2 ;;
    --image-key-left)           image_key_left="$2"; shift 2 ;;
    --image-key-right)          image_key_right="$2"; shift 2 ;;
    --action-dim)               action_dim="$2"; shift 2 ;;
    --proprio-dim)              proprio_dim="$2"; shift 2 ;;
    --num-actions-chunk)        num_actions_chunk="$2"; PROFILE_NUM_ACTIONS_CHUNK_SET=1; shift 2 ;;
    --num-images)               num_images_in_input="$2"; PROFILE_NUM_IMAGES_SET=1; shift 2 ;;
    --use-film)                 use_film="$2"; PROFILE_USE_FILM_SET=1; shift 2 ;;
    --use-proprio)              use_proprio="$2"; PROFILE_USE_PROPRIO_SET=1; shift 2 ;;
    --use-l1-regression)        use_l1_regression="$2"; shift 2 ;;
    --batch-size)               batch_size="$2"; PROFILE_BATCH_SIZE_SET=1; shift 2 ;;
    --learning-rate)            learning_rate="$2"; shift 2 ;;
    --num-steps-before-decay)   num_steps_before_decay="$2"; PROFILE_NUM_STEPS_BEFORE_DECAY_SET=1; shift 2 ;;
    --max-steps)                max_steps="$2"; PROFILE_MAX_STEPS_SET=1; shift 2 ;;
    --save-freq)                save_freq="$2"; PROFILE_SAVE_FREQ_SET=1; shift 2 ;;
    --lora-rank)                lora_rank="$2"; PROFILE_LORA_RANK_SET=1; shift 2 ;;
    --image-aug)                image_aug="$2"; PROFILE_IMAGE_AUG_SET=1; shift 2 ;;
    --num-gpus)                 num_gpus="$2"; PROFILE_NUM_GPUS_SET=1; shift 2 ;;
    --subscription-id)          subscription_id="$2"; shift 2 ;;
    --resource-group)           resource_group="$2"; shift 2 ;;
    --workspace-name)           workspace_name="$2"; shift 2 ;;
    --compute)                  compute="$2"; shift 2 ;;
    --instance-type)            instance_type="$2"; PROFILE_INSTANCE_TYPE_SET=1; shift 2 ;;
    --stream)                   stream_logs=true; shift ;;
    -a|--save-as)               save_as="$2"; shift 2 ;;
    --config-preview)           config_preview=true; shift ;;
    --)                         shift; forward_args=("$@"); break ;;
    *)                          fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$profile" ]] && apply_profile "$profile"

# -------- Validation --------
require_tools az
ensure_ml_extension

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

if [[ ${#blob_urls[@]} -eq 0 && -z "$dataset_asset" ]]; then
  [[ -z "$dataset_repo_id" ]] && fatal "--dataset-asset, --blob-url, or --dataset-repo-id is required"
else
  dataset_repo_id="${dataset_repo_id:-dataset}"
fi

display_name="${display_name:-${job_name}-$(date +%Y%m%d-%H%M%S)}"
experiment_name="${experiment_name:-openvla-oft-training}"

if [[ "$config_preview" == "true" ]]; then
  section "Submission Configuration"
  print_kv "Job name" "$job_name"
  print_kv "Display name" "$display_name"
  print_kv "Experiment" "$experiment_name"
  print_kv "Compute" "$compute"
  print_kv "Instance type" "$instance_type"
  print_kv "Image" "$image"
  print_kv "Dataset repo id" "$dataset_repo_id"
  print_kv "Dataset name (RLDS)" "$dataset_name"
  print_kv "Dataset asset" "${dataset_asset:-<none>}"
  print_kv "Blob URLs" "${blob_urls[*]:-<none>}"
  print_kv "Profile" "${profile:-<none>}"
  print_kv "VLA path" "$vla_path"
  print_kv "openvla-oft ref" "$openvla_oft_ref"
  print_kv "transformers fork ref" "$transformers_ref"
  print_kv "num_gpus (nproc-per-node)" "$num_gpus"
  print_kv "batch_size" "$batch_size"
  print_kv "learning_rate" "$learning_rate"
  print_kv "max_steps" "$max_steps"
  print_kv "lora_rank" "$lora_rank"
  print_kv "use_film/use_proprio/L1" "$use_film/$use_proprio/$use_l1_regression"
  exit 0
fi

# -------- Register environment --------
register_environment "$environment_name" "$environment_version" "$image" \
  "$resource_group" "$workspace_name" "$subscription_id"

if [[ "$assets_only" == "true" ]]; then
  info "Environment registered. Skipping job submission (--assets-only)."
  exit 0
fi

# -------- Submit job --------
blob_urls_json="[]"
if [[ ${#blob_urls[@]} -gt 0 ]]; then
  blob_urls_json=$(printf '%s\n' "${blob_urls[@]}" | jq -R . | jq -sc .)
fi

submit_args=(
  --file "$job_file"
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --subscription "$subscription_id"
  --name "$job_name"
  --set "display_name=$display_name"
  --set "experiment_name=$experiment_name"
  --set "compute=azureml:$compute"
  --set "environment=azureml:${environment_name}:${environment_version}"
  --set "resources.instance_type=$instance_type"
  --set "inputs.dataset_repo_id=$dataset_repo_id"
  --set "inputs.dataset_name=$dataset_name"
  --set "inputs.dataset_root=$dataset_root"
  --set "inputs.blob_urls=$blob_urls_json"
  --set "inputs.vla_path=$vla_path"
  --set "inputs.openvla_oft_ref=$openvla_oft_ref"
  --set "inputs.transformers_fork_ref=$transformers_ref"
  --set "inputs.image_key_primary=$image_key_primary"
  --set "inputs.image_key_left_wrist=$image_key_left"
  --set "inputs.image_key_right_wrist=$image_key_right"
  --set "inputs.action_dim=$action_dim"
  --set "inputs.proprio_dim=$proprio_dim"
  --set "inputs.num_actions_chunk=$num_actions_chunk"
  --set "inputs.num_images_in_input=$num_images_in_input"
  --set "inputs.use_film=$use_film"
  --set "inputs.use_proprio=$use_proprio"
  --set "inputs.use_l1_regression=$use_l1_regression"
  --set "inputs.batch_size=$batch_size"
  --set "inputs.learning_rate=$learning_rate"
  --set "inputs.num_steps_before_decay=$num_steps_before_decay"
  --set "inputs.max_steps=$max_steps"
  --set "inputs.save_freq=$save_freq"
  --set "inputs.lora_rank=$lora_rank"
  --set "inputs.image_aug=$image_aug"
  --set "inputs.num_gpus=$num_gpus"
  --set "command=bash training/il/scripts/openvla_oft/azureml-train-entry.sh"
  # Inputs flow to the entry script via env vars (the AzureML K8s extension does
  # not substitute \${{inputs.X}} placeholders inside `command:` for Download mounts).
  --set "environment_variables.DATASET_REPO_ID=$dataset_repo_id"
  --set "environment_variables.DATASET_ROOT=$dataset_root"
  --set "environment_variables.DATASET_NAME=$dataset_name"
  --set "environment_variables.VLA_PATH=$vla_path"
  --set "environment_variables.OPENVLA_OFT_REF=$openvla_oft_ref"
  --set "environment_variables.TRANSFORMERS_FORK_REF=$transformers_ref"
  --set "environment_variables.IMAGE_KEY_PRIMARY=$image_key_primary"
  --set "environment_variables.IMAGE_KEY_LEFT_WRIST=$image_key_left"
  --set "environment_variables.IMAGE_KEY_RIGHT_WRIST=$image_key_right"
  --set "environment_variables.ACTION_DIM=$action_dim"
  --set "environment_variables.PROPRIO_DIM=$proprio_dim"
  --set "environment_variables.NUM_ACTIONS_CHUNK=$num_actions_chunk"
  --set "environment_variables.NUM_IMAGES_IN_INPUT=$num_images_in_input"
  --set "environment_variables.USE_FILM=$use_film"
  --set "environment_variables.USE_PROPRIO=$use_proprio"
  --set "environment_variables.USE_L1_REGRESSION=$use_l1_regression"
  --set "environment_variables.BATCH_SIZE=$batch_size"
  --set "environment_variables.LEARNING_RATE=$learning_rate"
  --set "environment_variables.NUM_STEPS_BEFORE_DECAY=$num_steps_before_decay"
  --set "environment_variables.MAX_STEPS=$max_steps"
  --set "environment_variables.SAVE_FREQ=$save_freq"
  --set "environment_variables.LORA_RANK=$lora_rank"
  --set "environment_variables.IMAGE_AUG=$image_aug"
  --set "environment_variables.NUM_GPUS=$num_gpus"
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
)

# Mount a registered AzureML data asset (uri_folder) as `dataset_asset` input and
# export its mount path as DATASET_MOUNT for the entry script.
if [[ -n "$dataset_asset" ]]; then
  [[ "$dataset_asset" == azureml:* ]] || dataset_asset="azureml:$dataset_asset"
  submit_args+=(
    --set "inputs.dataset_asset.type=uri_folder"
    --set "inputs.dataset_asset.mode=ro_mount"
    --set "inputs.dataset_asset.path=$dataset_asset"
    --set 'environment_variables.DATASET_MOUNT=${{inputs.dataset_asset}}'
  )
fi

[[ "$stream_logs" == "true" ]] && submit_args+=(--stream)
[[ -n "$save_as" ]] && submit_args+=(--save-as "$save_as")

info "Submitting job ${job_name} to compute=${compute} (${instance_type})"
az ml job create "${submit_args[@]}" "${forward_args[@]}"

section "Deployment Summary"
print_kv "Job" "$job_name"
print_kv "Display name" "$display_name"
print_kv "Compute" "$compute"
print_kv "Instance type" "$instance_type"
print_kv "Environment" "${environment_name}:${environment_version}"
print_kv "Dataset" "$dataset_repo_id (-> $dataset_name)"
