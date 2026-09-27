#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "${SCRIPT_DIR}/../../.." && pwd))"

# shellcheck source=../../../scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "${REPO_ROOT}/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "${REPO_ROOT}/infrastructure/terraform" 2>/dev/null || true

show_help() {
  cat <<'EOF'
Usage: submit-azureml-groot-training.sh OPTIONS [-- az-ml-job-flags]

Submit NVIDIA GR00T N1.7 fine-tuning to Azure ML.

Required:
  --dataset-asset URI       Immutable Azure ML URI-folder data asset
                            (azureml:NAME:VERSION or full ARM-style URI).
  --compute NAME            Azure ML AmlCompute target.

Azure:
  --subscription-id ID      Azure subscription.
  --resource-group NAME     Azure ML resource group.
  --workspace-name NAME     Azure ML workspace.
  --environment-name NAME   Environment asset name.
  --environment-version VER Environment asset version.
  --image IMAGE             Digest-pinned base image.
  --assets-only             Register the environment without submitting a job.

Training:
  --batch-size N            Global batch size (default: 4).
  --max-steps N             Training steps (default: 100).
  --save-steps N            Checkpoint interval (default: 50).
  --dataloader-workers N    Data-loader workers (default: 4).
  --num-gpus N              Required visible GPU count (default: 2).
  --modality-config PATH    Local N1.7 modality config file. The submitter
                            embeds it into the immutable Azure ML job.
  --base-model ID           Hugging Face model ID.
  --base-model-revision SHA Immutable 40-character model commit.
  --model-cache-asset URI   Immutable AML URI-folder asset containing the
                            GR00T N1.7 and Cosmos backbone snapshots.
  --groot-ref SHA           Immutable 40-character Isaac-GR00T commit.
  --hf-token TOKEN          Hugging Face token for gated model access.
  --hf-token-secret-url URL Azure Key Vault secret URL resolved at runtime by
                            the compute managed identity (preferred).
  --preflight-only          Validate GPU, dataset, GR00T, and modality config.
  --resume-checkpoint URI   Resume optimizer, scheduler, and RNG state from a
                            versioned Azure ML URI-folder checkpoint output.
  --register-model NAME     Mirror the final checkpoint to an AML model version.

Job:
  --experiment-name NAME    Azure ML experiment name.
  --display-name NAME       Job display name.
  --stream                  Stream logs after submission.
  --config-preview          Print resolved configuration and exit.
  --save-as PATH            Save the final Azure ML job YAML.
EOF
}

environment_name="${ENVIRONMENT_NAME:-groot-n17-training-env}"
environment_version="${ENVIRONMENT_VERSION:-}"
environment_version_explicit=false
[[ -n "${ENVIRONMENT_VERSION:-}" ]] && environment_version_explicit=true
image="${IMAGE:-${DEFAULT_GROOT_IMAGE}}"
assets_only=false

job_file="${REPO_ROOT}/training/vla/workflows/azureml/groot-n17-train.yaml"
dataset_asset="${DATASET_ASSET:-}"
compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

batch_size="${BATCH_SIZE:-4}"
max_steps="${MAX_STEPS:-100}"
save_steps="${SAVE_STEPS:-50}"
dataloader_workers="${DATALOADER_WORKERS:-4}"
num_gpus="${NUM_GPUS:-2}"
modality_config="${MODALITY_CONFIG_PATH:-}"
base_model="${BASE_MODEL:-nvidia/GR00T-N1.7-3B}"
base_model_revision="${BASE_MODEL_REVISION:-2fc962b973bccdd5d8ce4f67cc63b264d6886495}"
model_cache_asset="${MODEL_CACHE_ASSET:-}"
groot_ref="${ISAAC_GROOT_REF:-23ace64f17aa5015259b8609d371eb61a357c776}"
hf_token="${HF_TOKEN:-}"
hf_token_secret_url="${HF_TOKEN_SECRET_URL:-}"
preflight_only=false
resume_checkpoint=""
register_model=""
experiment_name="groot-n17-training"
display_name=""
stream_logs=false
config_preview=false
save_as=""
forward_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) show_help; exit 0 ;;
    --dataset-asset) dataset_asset="$2"; shift 2 ;;
    --compute) compute="$2"; shift 2 ;;
    --subscription-id) subscription_id="$2"; shift 2 ;;
    --resource-group) resource_group="$2"; shift 2 ;;
    --workspace-name) workspace_name="$2"; shift 2 ;;
    --environment-name) environment_name="$2"; shift 2 ;;
    --environment-version) environment_version="$2"; environment_version_explicit=true; shift 2 ;;
    --image|-i) image="$2"; shift 2 ;;
    --assets-only) assets_only=true; shift ;;
    --batch-size) batch_size="$2"; shift 2 ;;
    --max-steps) max_steps="$2"; shift 2 ;;
    --save-steps) save_steps="$2"; shift 2 ;;
    --dataloader-workers) dataloader_workers="$2"; shift 2 ;;
    --num-gpus) num_gpus="$2"; shift 2 ;;
    --modality-config) modality_config="$2"; shift 2 ;;
    --base-model) base_model="$2"; shift 2 ;;
    --base-model-revision) base_model_revision="$2"; shift 2 ;;
    --model-cache-asset) model_cache_asset="$2"; shift 2 ;;
    --groot-ref) groot_ref="$2"; shift 2 ;;
    --hf-token) hf_token="$2"; shift 2 ;;
    --hf-token-secret-url) hf_token_secret_url="$2"; shift 2 ;;
    --preflight-only) preflight_only=true; shift ;;
    --resume-checkpoint) resume_checkpoint="$2"; shift 2 ;;
    --register-model) register_model="$2"; shift 2 ;;
    --experiment-name) experiment_name="$2"; shift 2 ;;
    --display-name) display_name="$2"; shift 2 ;;
    --stream) stream_logs=true; shift ;;
    --config-preview) config_preview=true; shift ;;
    --save-as) save_as="$2"; shift 2 ;;
    --) shift; forward_args=("$@"); break ;;
    *) fatal "Unknown option: $1" ;;
  esac
done

if [[ "${environment_version_explicit}" != "true" ]]; then
  environment_version="$(derive_azureml_environment_version_from_image "${image}")"
fi

require_tools az base64 tr
require_az_extension ml

[[ -n "${subscription_id}" ]] || fatal "--subscription-id is required"
[[ -n "${resource_group}" ]] || fatal "--resource-group is required"
[[ -n "${workspace_name}" ]] || fatal "--workspace-name is required"
[[ -n "${compute}" ]] || fatal "--compute is required"
[[ -n "${dataset_asset}" || "${assets_only}" == "true" ]] || fatal "--dataset-asset is required"
[[ -f "${job_file}" ]] || fatal "Job template not found: ${job_file}"
[[ -n "${modality_config}" || "${assets_only}" == "true" ]] || fatal "--modality-config is required"
if [[ -n "${modality_config}" ]]; then
  [[ -f "${modality_config}" ]] || fatal "--modality-config not found: ${modality_config}"
fi

positive_integer() {
  local name="$1" value="$2"
  [[ "${value}" =~ ^[1-9][0-9]*$ ]] || fatal "${name} must be a positive integer (got '${value}')"
}
positive_integer "--batch-size" "${batch_size}"
positive_integer "--max-steps" "${max_steps}"
positive_integer "--save-steps" "${save_steps}"
positive_integer "--dataloader-workers" "${dataloader_workers}"
positive_integer "--num-gpus" "${num_gpus}"
(( batch_size % num_gpus == 0 )) || fatal \
  "--batch-size is the global batch and must be divisible by --num-gpus"

[[ "${groot_ref}" =~ ^[0-9a-fA-F]{40}$ ]] || fatal "--groot-ref must be an immutable 40-hex commit"
[[ "${base_model_revision}" =~ ^[0-9a-fA-F]{40}$ ]] || fatal \
  "--base-model-revision must be an immutable 40-hex commit"
[[ -z "${hf_token}" || -z "${hf_token_secret_url}" ]] || fatal \
  "--hf-token and --hf-token-secret-url are mutually exclusive"
if [[ -n "${hf_token_secret_url}" ]]; then
  [[ "${hf_token_secret_url}" =~ ^https://[A-Za-z0-9-]+\.vault\.azure\.net/secrets/[A-Za-z0-9-]+(/[A-Za-z0-9]+)?$ ]] || \
    fatal "--hf-token-secret-url must be a versioned or unversioned Azure Key Vault secret URL"
fi
if [[ "${preflight_only}" != "true" && "${base_model}" == "nvidia/GR00T-N1.7-3B" ]]; then
  [[ -n "${model_cache_asset}" || -n "${hf_token}" || -n "${hf_token_secret_url}" ]] || fatal \
    "GR00T N1.7 loads gated nvidia/Cosmos-Reason2-2B; provide approved Hugging Face access via --hf-token-secret-url"
fi

if [[ -n "${dataset_asset}" ]]; then
  case "${dataset_asset}" in
    azureml://*/data/*/versions/*) asset_version="${dataset_asset##*/versions/}" ;;
    azureml:*:*) asset_version="${dataset_asset##*:}" ;;
    *) fatal "--dataset-asset must use azureml:NAME:VERSION or a full versioned Azure ML data URI" ;;
  esac
  [[ "${asset_version}" =~ ^(0|[1-9][0-9]*)$ ]] || fatal \
    "--dataset-asset version must be a canonical integer (got '${dataset_asset}')"
fi
if [[ -n "${model_cache_asset}" ]]; then
  case "${model_cache_asset}" in
    azureml://*/data/*/versions/*) model_asset_version="${model_cache_asset##*/versions/}" ;;
    azureml:*:*) model_asset_version="${model_cache_asset##*:}" ;;
    *) fatal "--model-cache-asset must use azureml:NAME:VERSION or a full versioned Azure ML data URI" ;;
  esac
  [[ "${model_asset_version}" =~ ^(0|[1-9][0-9]*)$ ]] || fatal \
    "--model-cache-asset version must be a canonical integer"
fi

if [[ -n "${register_model}" ]]; then
  [[ "${register_model}" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,254}$ ]] || fatal \
    "--register-model contains invalid Azure ML model-name characters"
  [[ "${preflight_only}" != "true" ]] || fatal "--register-model cannot be combined with --preflight-only"
fi
[[ -z "${resume_checkpoint}" || "${preflight_only}" != "true" ]] || \
  fatal "--resume-checkpoint cannot be combined with --preflight-only"

if [[ "${config_preview}" == "true" ]]; then
  section "GR00T N1.7 Azure ML Configuration"
  print_kv "Dataset" "${dataset_asset:-<assets-only>}"
  print_kv "Compute" "${compute}"
  print_kv "GPUs" "${num_gpus}"
  print_kv "Batch Size" "${batch_size}"
  print_kv "Max Steps" "${max_steps}"
  print_kv "Modality Config" "${modality_config}"
  print_kv "Base Model" "${base_model}@${base_model_revision}"
  print_kv "Model Cache" "${model_cache_asset:-<none>}"
  print_kv "Isaac-GR00T" "${groot_ref}"
  print_kv "Preflight Only" "${preflight_only}"
  print_kv "Resume Checkpoint" "${resume_checkpoint:-<none>}"
  print_kv "Register Model" "${register_model:-<none>}"
  print_kv "Environment" "${environment_name}:${environment_version}"
  exit 0
fi

register_azureml_environment "${environment_name}" "${environment_version}" "${image}" \
  "${resource_group}" "${workspace_name}" "${subscription_id}"
info "Environment: ${environment_name}:${environment_version}"

if [[ "${assets_only}" == "true" ]]; then
  info "Assets prepared; skipping job submission per --assets-only"
  exit 0
fi

managed_identity_client_id="$(resolve_azureml_compute_identity_client_id \
  "${compute}" "${resource_group}" "${workspace_name}")"
modality_config_b64="$(base64 < "${modality_config}" | tr -d '\n')"
resolved_job_file="${job_file}"
if [[ -n "${resume_checkpoint}" || -n "${model_cache_asset}" ]]; then
  require_tools cp mktemp sed
  resolved_job_file="$(mktemp)"
  trap 'rm -f "${resolved_job_file}"' EXIT
  cp "${job_file}" "${resolved_job_file}"
fi
if [[ -n "${resume_checkpoint}" ]]; then
  sed -i \
    -e '/^command: >-$/i\
  resume_checkpoint:\
    type: uri_folder\
    mode: ro_mount\
' \
    -e 's|RESUME_CHECKPOINT="${{inputs.dataset}}"|RESUME_CHECKPOINT="${{inputs.resume_checkpoint}}"|' \
    "${resolved_job_file}"
fi
if [[ -n "${model_cache_asset}" ]]; then
  sed -i \
    -e '/^command: >-$/i\
  model_cache:\
    type: uri_folder\
    mode: ro_mount\
' \
    -e 's|MODEL_CACHE_ROOT=""|MODEL_CACHE_ROOT="${{inputs.model_cache}}"|' \
    "${resolved_job_file}"
fi

az_args=(
  az ml job create
  --resource-group "${resource_group}"
  --workspace-name "${workspace_name}"
  --file "${resolved_job_file}"
  --set "code=${REPO_ROOT}/training"
  --set "environment=azureml:${environment_name}:${environment_version}"
  --set "compute=${compute}"
  --set "inputs.dataset.path=${dataset_asset}"
  --set "experiment_name=${experiment_name}"
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=${subscription_id}"
  --set "environment_variables.AZURE_RESOURCE_GROUP=${resource_group}"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=${workspace_name}"
  --set "environment_variables.MODALITY_CONFIG_B64=${modality_config_b64}"
  --set "environment_variables.BASE_MODEL=${base_model}"
  --set "environment_variables.BASE_MODEL_REVISION=${base_model_revision}"
  --set "environment_variables.ISAAC_GROOT_REF=${groot_ref}"
  --set "environment_variables.BATCH_SIZE=${batch_size}"
  --set "environment_variables.MAX_STEPS=${max_steps}"
  --set "environment_variables.SAVE_STEPS=${save_steps}"
  --set "environment_variables.DATALOADER_WORKERS=${dataloader_workers}"
  --set "environment_variables.NUM_GPUS=${num_gpus}"
  --set "environment_variables.PREFLIGHT_ONLY=${preflight_only}"
)

[[ -n "${managed_identity_client_id}" ]] && \
  az_args+=(--set "environment_variables.AZURE_CLIENT_ID=${managed_identity_client_id}")
[[ -n "${display_name}" ]] && az_args+=(--set "display_name=${display_name}")
[[ -n "${hf_token}" ]] && az_args+=(--set "environment_variables.HF_TOKEN=${hf_token}")
[[ -n "${hf_token_secret_url}" ]] && \
  az_args+=(--set "environment_variables.HF_TOKEN_SECRET_URL=${hf_token_secret_url}")
[[ -n "${model_cache_asset}" ]] && \
  az_args+=(--set "inputs.model_cache.path=${model_cache_asset}")
if [[ -n "${resume_checkpoint}" ]]; then
  az_args+=(
    --set "inputs.resume_checkpoint.path=${resume_checkpoint}"
    --set "environment_variables.RESUME=true"
  )
fi
if [[ -n "${register_model}" ]]; then
  az_args+=(
    --set "environment_variables.AZURE_UPLOAD=true"
    --set "environment_variables.AZUREML_MODEL_NAME=${register_model}"
  )
fi
[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
[[ -n "${save_as}" ]] && az_args+=(--save-as "${save_as}")
az_args+=(--query name --output tsv)

info "Submitting Azure ML GR00T N1.7 job..."
job_name="$("${az_args[@]}")" || fatal "Azure ML job submission failed"
info "Job submitted: ${job_name}"
info "Portal: https://ml.azure.com/runs/${job_name}?wsid=/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.MachineLearningServices/workspaces/${workspace_name}"

if [[ "${stream_logs}" == "true" ]]; then
  az ml job stream --name "${job_name}" \
    --resource-group "${resource_group}" --workspace-name "${workspace_name}"
fi
