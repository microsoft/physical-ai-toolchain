#!/usr/bin/env bash
# Submit adapter-resolved LeRobot VLA training to Azure ML
# Uses the shared VLA dependency lock and policy-agnostic LeRobot entrypoint.
# cspell:ignore alreadyexists
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: submit-azureml-vla-training.sh [OPTIONS] [-- az-ml-job-flags]

Submit adapter-resolved LeRobot VLA training to Azure ML.

OPTIONS:
    -d, --dataset-repo-id ID     Dataset logical name for folder naming (default: dataset)
      --dataset-revision SHA   Full 40-character dataset commit for Hugging Face sources

DATA SOURCE (combinable):
        --dataset-asset URI           AzureML data asset to mount (ro_mount, repeatable, max 64).
                                      Accepted forms:
                                        azureml:NAME:VERSION       (numeric version required)
                                        azureml://.../data/NAME/versions/VERSION
                                      Shorthands like azureml:NAME or azureml:NAME@latest
                                      are rejected to keep runs reproducible.
                                      Mounted read-only via AzureML native mount.
        --blob-url URL                Add blob dataset URL (repeatable; use multiple times for merge)
        --dataset-root DIR            Container path where datasets are materialized
                                      (default: /workspace/data)

AZUREML ASSET OPTIONS:
    --environment-name NAME       AzureML environment name (default: vla-training-env)
    --environment-version VER     Environment version (default: derived from --image)
    --image IMAGE                 Container image (default: $DEFAULT_LEROBOT_TRAIN_IMAGE, digest-pinned in scripts/lib/common.sh)
    --assets-only                 Register environment without submitting job
    --validate-only               Validate the composed job without registering assets or submitting

TRAINING OPTIONS:
    -w, --job-file PATH           Job YAML template (default: training/vla/workflows/azureml/vla-train.yaml)
      --adapter NAME            Required model adapter: lerobot-pi or lerobot-smolvla
    -p, --policy-type TYPE        Optional adapter policy alias
    -j, --job-name NAME           Job identifier (default: vla-training)
    -o, --output-dir DIR          Container output directory (default: /workspace/outputs/train)
        --policy-repo-id ID       HuggingFace Hub repo_id forwarded to lerobot as
                                  policy.repo_id. lerobot uses this for hub-push
                                  naming and checkpoint metadata; the entry script
                                  passes --policy.push_to_hub=false so no upload
                                  occurs. To warm-start from an existing AzureML-
                                  registered model use --init-from-policy-model
                                  instead.
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
        --init-from-policy-hf-repo ID
                                  Download a Hugging Face policy repository directly
                                  inside the Azure ML job.
        --init-from-policy-hf-revision SHA
                                  Full 40-character lowercase Git commit for the
                                  direct Hugging Face policy repository.
        --lerobot-version VER     Specific LeRobot version or "latest" (default: latest)
        --lerobot-project PATH    Override the LeRobot dependency project, given as
                                  a repo-relative path (default:
                                  training/vla/lerobot). The directory must contain
                                  pyproject.toml and uv.lock inside training/ so it
                                  ships with the code asset; absolute paths are
                                  rejected.

TRAINING HYPERPARAMETERS:
        --training-steps N        Total training iterations
        --batch-size N            Training batch size
        --eval-freq N             Evaluation frequency
        --save-freq N             Checkpoint save frequency (default: 5000)
        --log-freq N              MLflow metric log frequency (default: 200)
        --rename-map JSON         Map dataset feature names to policy feature names,
                                  for example:
                                  '{"observation.images.d435":"observation.images.base_0_rgb"}'

CHECKPOINT REGISTRATION:
    -r, --register-checkpoint NAME  Model name for Azure ML registration

AZURE CONTEXT:
        --subscription-id ID      Azure subscription ID
        --resource-group NAME     Azure resource group
        --workspace-name NAME     Azure ML workspace
        --compute TARGET          Compute target override
        --hf-key-vault-url URL    Azure Key Vault URL containing the Hugging Face
                access token (default: $HF_KEY_VAULT_URL).
        --hf-token-secret-name NAME
                Key Vault secret name for the Hugging Face access
                token (default: $HF_TOKEN_SECRET_NAME). The job
                retrieves the value with its managed identity;
                the token is never included in the job spec.
        --instance-type NAME      Instance type for AzureML-on-Kubernetes compute
                                  (default: gpu). The selected adapter validates
                                  model-family precision and trainable-scope
                                  options. The instance type value is
                                  forwarded to the job as resources.instance_type
                                  whenever non-empty. On AzureML managed
                                  AmlCompute the cluster's VM SKU determines GPU
                                  count, so pass --instance-type '' to omit
                                  the field. The training wrapper auto-detects
                                  the visible GPU count via
                                  torch.cuda.device_count() on both paths and
                                  enables Accelerate multi-GPU launch when N>1.
                                  Shipped multi-GPU Kubernetes types:
                                  gpu2/gpuspot2, gpu4/gpuspot4 (see
                                  infrastructure/setup/manifests/azureml-instance-types.yaml).
        --train-expert-only       Train only the model-family expert scope
        --no-train-expert-only    Disable expert-only training when supported
        --mixed-precision MODE    Accelerate mixed-precision mode (no|fp16|bf16);
                default: bf16. Explicit mixed
                                  precision uses Accelerate on single- and
                                  multi-GPU jobs.
        --policy-dtype DTYPE      Adapter-specific policy storage dtype
        --gradient-checkpointing  Adapter-specific activation recomputation
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
    # PI training with a Hugging Face dataset
    submit-azureml-vla-training.sh --adapter lerobot-pi -p pi0 \
      -d lerobot/aloha_sim_insertion_human

    # SmolVLA bounded optimizer-step smoke
    submit-azureml-vla-training.sh \
      --adapter lerobot-smolvla \
      -d lerobot/svla_so100_pickplace \
      --dataset-revision 728583b5eaf9e739a7f119e2def466fa1d552402 \
      --training-steps 2 \
      --batch-size 1

    # pi0_fast with custom hyperparameters
    submit-azureml-vla-training.sh \
      --adapter lerobot-pi \
      -d user/custom-dataset \
      -p pi0_fast \
      --training-steps 50000 \
      --batch-size 8

    # Warm-start a new pi0 run from a previously registered checkpoint
    submit-azureml-vla-training.sh --adapter lerobot-pi \
      -d user/dataset \
      --init-from-policy-model azureml:vla-pi0:7

    # Register trained pi0 model and stream logs
    submit-azureml-vla-training.sh --adapter lerobot-pi \
      -d user/dataset \
      -r my-pi0-model \
      --stream

    # Fine-tune from pre-trained pi0 policy
    submit-azureml-vla-training.sh --adapter lerobot-pi \
      -d user/dataset \
      --policy-repo-id user/pretrained-pi0 \
      --training-steps 10000

    # AzureML data asset (native mount, no download)
    submit-azureml-vla-training.sh --adapter lerobot-pi \
      --dataset-asset azureml:pusht-episodes:3 \
      -r pusht-pi0-model

    # Single-node multi-GPU training (4 GPUs on a gpu4 InstanceType, bf16)
    submit-azureml-vla-training.sh --adapter lerobot-pi \
      -d user/dataset \
      --instance-type gpu4 \
      --mixed-precision bf16 \
      --batch-size 4

    # Register environment only (no job submission)
    submit-azureml-vla-training.sh --adapter lerobot-pi -d placeholder --assets-only
EOF
}

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

render_job_file_with_mounted_inputs() {
  local source_file="$1" rendered_file="$2" init_model="$3"
  shift 3

  python3 - "$source_file" "$rendered_file" "$init_model" "$@" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
init_model = sys.argv[3]
dataset_assets = sys.argv[4:]
lines = source.read_text(encoding="utf-8").splitlines()

try:
    inputs_index = next(i for i, line in enumerate(lines) if line.rstrip() == "inputs:")
except StopIteration:
    raise SystemExit(f"Job YAML has no top-level inputs: block: {source}")

inserted = []
if init_model:
    inserted.extend(
        [
            "  init_from_policy_model:",
            "    type: custom_model",
            "    mode: download",
            f"    path: {json.dumps(init_model)}",
        ]
    )
for index, asset in enumerate(dataset_assets):
    inserted.extend(
        [
            f"  dataset_asset_{index}:",
            "    type: uri_folder",
            "    mode: ro_mount",
            f"    path: {json.dumps(asset)}",
        ]
    )

target.write_text("\n".join(lines[: inputs_index + 1] + inserted + lines[inputs_index + 1 :]) + "\n", encoding="utf-8")
PY
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

environment_name="vla-training-env"
environment_version="${ENVIRONMENT_VERSION:-}"
environment_version_explicit=false
[[ -n "${ENVIRONMENT_VERSION:-}" ]] && environment_version_explicit=true
image="${IMAGE:-$DEFAULT_LEROBOT_TRAIN_IMAGE}"
assets_only=false
validate_only=false

job_file="$REPO_ROOT/training/vla/workflows/azureml/vla-train.yaml"
dataset_repo_id="${DATASET_REPO_ID:-}"
dataset_revision="${DATASET_REVISION:-}"
adapter_name="${VLA_MODEL_ADAPTER:-}"
policy_type="${POLICY_TYPE:-}"
job_name="${JOB_NAME:-vla-training}"
output_dir="${OUTPUT_DIR:-/workspace/outputs/train}"
policy_repo_id="${POLICY_REPO_ID:-}"
init_from_policy_model="${INIT_FROM_POLICY_MODEL:-}"
init_from_policy_hf_repo_id="${INIT_FROM_POLICY_HF_REPO_ID:-}"
init_from_policy_hf_revision="${INIT_FROM_POLICY_HF_REVISION:-}"
lerobot_version="${LEROBOT_VERSION:-}"
lerobot_project="${LEROBOT_PROJECT:-training/vla/lerobot}"

dataset_asset_count_max=64
dataset_assets=()
blob_urls=()
dataset_root="${DATASET_ROOT:-/workspace/data}"

training_steps="${TRAINING_STEPS:-}"
batch_size="${BATCH_SIZE:-}"
eval_freq="${EVAL_FREQ:-}"
save_freq="${SAVE_FREQ:-5000}"
log_freq="${LOG_FREQ:-}"
rename_map="${RENAME_MAP:-}"

register_checkpoint="${REGISTER_CHECKPOINT:-}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
mlflow_retries="${MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES:-3}"
mlflow_timeout="${MLFLOW_HTTP_REQUEST_TIMEOUT:-60}"

compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
instance_type="gpu"
train_expert_only=""
mixed_precision="${MIXED_PRECISION:-bf16}"
policy_dtype="${POLICY_DTYPE:-}"
gradient_checkpointing=false
hf_key_vault_url="${HF_KEY_VAULT_URL:-}"
hf_token_secret_name="${HF_TOKEN_SECRET_NAME:-}"
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
    --environment-version)        environment_version="$2"; environment_version_explicit=true; shift 2 ;;
    --image|-i)                   image="$2"; shift 2 ;;
    --assets-only)                assets_only=true; shift ;;
    --validate-only)              validate_only=true; shift ;;
    -w|--job-file)                job_file="$2"; shift 2 ;;
    --adapter)                    adapter_name="$2"; shift 2 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    --dataset-revision)           dataset_revision="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -o|--output-dir)              output_dir="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    --init-from-policy-model)     init_from_policy_model="$2"; shift 2 ;;
    --init-from-policy-hf-repo)   init_from_policy_hf_repo_id="$2"; shift 2 ;;
    --init-from-policy-hf-revision) init_from_policy_hf_revision="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --lerobot-project)            lerobot_project="$2"; shift 2 ;;
    --dataset-asset)              dataset_assets+=("$2"); shift 2 ;;
    --blob-url)                   blob_urls+=("$2"); shift 2 ;;
    --dataset-root)               dataset_root="$2"; shift 2 ;;
    --training-steps)             training_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --eval-freq)                  eval_freq="$2"; shift 2 ;;
    --save-freq)                  save_freq="$2"; shift 2 ;;
    --log-freq)                   log_freq="$2"; shift 2 ;;
    --rename-map)                 rename_map="$2"; shift 2 ;;
    -r|--register-checkpoint)     register_checkpoint="$2"; shift 2 ;;
    --subscription-id)            subscription_id="$2"; shift 2 ;;
    --resource-group)             resource_group="$2"; shift 2 ;;
    --workspace-name)             workspace_name="$2"; shift 2 ;;
    --mlflow-token-retries)       mlflow_retries="$2"; shift 2 ;;
    --mlflow-http-timeout)        mlflow_timeout="$2"; shift 2 ;;
    --compute)                    compute="$2"; shift 2 ;;
    --instance-type)              instance_type="$2"; shift 2 ;;
    --train-expert-only)          train_expert_only=true; shift ;;
    --no-train-expert-only)       train_expert_only=false; shift ;;
    --mixed-precision)            mixed_precision="$2"; shift 2 ;;
    --policy-dtype)               policy_dtype="$2"; shift 2 ;;
    --gradient-checkpointing)     gradient_checkpointing=true; shift ;;
    --hf-key-vault-url)           hf_key_vault_url="$2"; shift 2 ;;
    --hf-token-secret-name)       hf_token_secret_name="$2"; shift 2 ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    --display-name)               display_name="$2"; shift 2 ;;
    --stream)                     stream_logs=true; shift ;;
    -a|--save-as)                 save_as="$2"; shift 2 ;;
    --config-preview)             config_preview=true; shift ;;
    --)                           shift; forward_args=("$@"); break ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

if [[ "$environment_version_explicit" != "true" ]]; then
  environment_version="$(derive_azureml_environment_version_from_image "$image")"
fi

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools az python3 base64
require_az_extension ml

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

if [[ ${#dataset_assets[@]} -gt 0 || ${#blob_urls[@]} -gt 0 ]]; then
  dataset_repo_id="${dataset_repo_id:-dataset}"
elif [[ -z "$dataset_repo_id" ]]; then
  fatal "No dataset source specified. Use --dataset-repo-id for HuggingFace Hub, or provide one or more --blob-url / --dataset-asset sources."
fi

if [[ ${#dataset_assets[@]} -eq 0 && ${#blob_urls[@]} -eq 0 ]]; then
  [[ "$dataset_revision" =~ ^[0-9a-f]{40}$ ]] || fatal \
    "--dataset-revision must be a full 40-character lowercase Git commit for Hugging Face datasets"
elif [[ -n "$dataset_revision" ]]; then
  fatal "--dataset-revision applies only to Hugging Face dataset sources"
fi

[[ -n "$adapter_name" ]] || fatal "--adapter is required (use: lerobot-pi or lerobot-smolvla)"

adapter_rename_map="$rename_map"
[[ -n "$adapter_rename_map" ]] || adapter_rename_map="{}"
adapter_args=(
  "$SCRIPT_DIR/model_adapters.py"
  --adapter "$adapter_name"
  --mixed-precision "$mixed_precision"
  --rename-map "$adapter_rename_map"
)
[[ -n "$policy_type" ]] && adapter_args+=(--policy-type "$policy_type")
[[ -n "$policy_dtype" ]] && adapter_args+=(--policy-dtype "$policy_dtype")
[[ "$gradient_checkpointing" == "true" ]] && adapter_args+=(--gradient-checkpointing)
if [[ "$train_expert_only" == "true" ]]; then
  adapter_args+=(--train-expert-only)
elif [[ "$train_expert_only" == "false" ]]; then
  adapter_args+=(--no-train-expert-only)
fi

adapter_resolution=$(python3 "${adapter_args[@]}") || fatal "Model adapter validation failed: $adapter_name"
adapter_values=$(python3 -c '
import json
import sys

resolution = json.loads(sys.argv[1])
environment = resolution["environment"]
values = (
    resolution["adapter_name"],
    resolution["adapter_version"],
    resolution["policy_type"],
    resolution["dependency_extra"],
    resolution["default_source_model"] or "",
    resolution["default_source_revision"] or "",
    str(resolution["requires_hf_token"]).lower(),
    environment["TRAIN_EXPERT_ONLY"],
    environment.get("GRADIENT_CHECKPOINTING", "false"),
    environment["USE_IMAGENET_STATS"],
    resolution["config_sha256"],
)
print("\n".join(values))
' "$adapter_resolution") || fatal "Unable to read model adapter resolution: $adapter_name"
mapfile -t resolved_adapter_values <<< "$adapter_values"

adapter_name="${resolved_adapter_values[0]}"
adapter_version="${resolved_adapter_values[1]}"
policy_type="${resolved_adapter_values[2]}"
dependency_extra="${resolved_adapter_values[3]}"
default_source_model="${resolved_adapter_values[4]}"
default_source_revision="${resolved_adapter_values[5]}"
adapter_requires_hf_token="${resolved_adapter_values[6]}"
train_expert_only="${resolved_adapter_values[7]}"
gradient_checkpointing="${resolved_adapter_values[8]}"
use_imagenet_stats="${resolved_adapter_values[9]}"
adapter_config_sha256="${resolved_adapter_values[10]}"

if [[ -z "$init_from_policy_model" && -z "$init_from_policy_hf_repo_id" && -z "$policy_repo_id" && -n "$default_source_model" ]]; then
  init_from_policy_hf_repo_id="$default_source_model"
  init_from_policy_hf_revision="$default_source_revision"
fi

if [[ -n "$init_from_policy_model" && -n "$policy_repo_id" ]]; then
  fatal "--init-from-policy-model and --policy-repo-id are mutually exclusive"
fi

if [[ -n "${HF_TOKEN:-}" ]]; then
  fatal "HF_TOKEN plaintext input is not supported. Store the token in Azure Key Vault and use --hf-key-vault-url with --hf-token-secret-name."
fi

if [[ -n "$hf_key_vault_url" || -n "$hf_token_secret_name" ]]; then
  [[ -n "$hf_key_vault_url" && -n "$hf_token_secret_name" ]] || fatal \
    "--hf-key-vault-url and --hf-token-secret-name must be provided together"
  [[ "$hf_key_vault_url" =~ ^https://[A-Za-z0-9-]+\.vault\.azure\.net/?$ ]] || fatal \
    "--hf-key-vault-url must use https://VAULT.vault.azure.net/"
  [[ "$hf_token_secret_name" =~ ^[A-Za-z0-9-]{1,127}$ ]] || fatal \
    "--hf-token-secret-name must contain 1-127 alphanumeric or hyphen characters"
fi

if [[ -n "$init_from_policy_hf_repo_id" || -n "$init_from_policy_hf_revision" ]]; then
  [[ -n "$init_from_policy_hf_repo_id" && -n "$init_from_policy_hf_revision" ]] || fatal \
    "--init-from-policy-hf-repo and --init-from-policy-hf-revision must be provided together"
  [[ "$init_from_policy_hf_repo_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal \
    "--init-from-policy-hf-repo must be a Hugging Face repository ID in OWNER/NAME form"
  [[ "$init_from_policy_hf_revision" =~ ^[0-9a-f]{40}$ ]] || fatal \
    "--init-from-policy-hf-revision must be a full 40-character lowercase Git commit"
  if [[ "$adapter_requires_hf_token" == "true" ]]; then
    [[ -n "$hf_token_secret_name" ]] || fatal \
      "--hf-key-vault-url and --hf-token-secret-name are required by adapter $adapter_name"
  fi
fi

if [[ -n "$init_from_policy_hf_repo_id" && ( -n "$init_from_policy_model" || -n "$policy_repo_id" ) ]]; then
  fatal "--init-from-policy-hf-repo is mutually exclusive with --init-from-policy-model and --policy-repo-id"
fi

case "$mixed_precision" in
  no|fp16|bf16) ;;
  *) fatal "--mixed-precision must be one of: no, fp16, bf16 (got '$mixed_precision')" ;;
esac

case "$policy_dtype" in
  ""|float32|bfloat16) ;;
  *) fatal "--policy-dtype must be one of: float32, bfloat16 (got '$policy_dtype')" ;;
esac

# AzureML model names: alphanumeric, dash, dot, underscore; must start with an
# alphanumeric or underscore; max 255 chars. Reject upfront so az ml model
# create at the end of training does not fail with a cryptic 400.
if [[ -n "$register_checkpoint" ]]; then
  [[ "$register_checkpoint" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,254}$ ]] || fatal \
    "--register-checkpoint: invalid model name '$register_checkpoint'. AzureML model names must start with an alphanumeric or underscore and contain only [A-Za-z0-9._-] (max 255 chars)."
fi

# --compute is effectively required because the job YAML's placeholder
# compute (azureml:cpu-cluster) is not a real target in user workspaces.
# Fail fast instead of letting az ml job create return a late, cryptic error.
[[ -n "$compute" ]] || fatal "--compute is required (or set AZUREML_COMPUTE env var, or expose 'compute_target' via Terraform outputs)."
[[ "$assets_only" != "true" || "$validate_only" != "true" ]] || fatal \
  "--assets-only and --validate-only are mutually exclusive"
[[ "$validate_only" != "true" || -z "$save_as" ]] || fatal \
  "--save-as is not supported with --validate-only"

[[ ${#dataset_assets[@]} -le $dataset_asset_count_max ]] || fatal \
  "--dataset-asset: too many data assets (${#dataset_assets[@]}); maximum is ${dataset_asset_count_max}."

# Accept only fully-qualified, version-pinned URIs for data assets.
# Version must be a canonical positive integer or "0" — leading zeros are
# rejected to keep the asset URI canonical with AzureML's stored form.
_VALID_VERSION_RE='^([1-9][0-9]*|0)$'
if [[ ${#dataset_assets[@]} -gt 0 ]]; then
  for _asset in "${dataset_assets[@]}"; do
    case "$_asset" in
      azureml://*/data/*/versions/*)
        version="${_asset##*/versions/}"
        [[ "$version" =~ $_VALID_VERSION_RE ]] || fatal \
          "--dataset-asset: version must be a canonical integer with no leading zeros (got '$_asset'). Use azureml://.../data/NAME/versions/VERSION."
        ;;
      azureml:*:*)
        version="${_asset##*:}"
        [[ "$version" =~ $_VALID_VERSION_RE ]] || fatal \
          "--dataset-asset: version must be a canonical integer with no leading zeros (got '$_asset'). Use azureml:NAME:VERSION; @latest and shorthands are not accepted."
        ;;
      *)
        fatal "--dataset-asset: unsupported URI form '$_asset'. Use azureml:NAME:VERSION or azureml://.../data/NAME/versions/VERSION."
        ;;
    esac
  done
fi

# Accept only fully-qualified, version-pinned URIs. Reject @latest and bare
# azureml:NAME so reruns of the same job spec do not silently drift to a newer
# registered model.
if [[ -n "$init_from_policy_model" ]]; then
  case "$init_from_policy_model" in
    azureml://*/models/*/versions/*)
      version="${init_from_policy_model##*/versions/}"
      [[ "$version" =~ $_VALID_VERSION_RE ]] || fatal \
        "--init-from-policy-model: version must be a canonical integer with no leading zeros (got '$init_from_policy_model')."
      ;;
    https://*) ;;
    azureml:*:*)
      version="${init_from_policy_model##*:}"
      [[ "$version" =~ $_VALID_VERSION_RE ]] || fatal \
        "--init-from-policy-model: version must be a canonical integer with no leading zeros (got '$init_from_policy_model'). Use azureml:NAME:VERSION; @latest and shorthands are not accepted."
      ;;
    *)
      fatal "--init-from-policy-model: unsupported URI form '$init_from_policy_model'. Use azureml:NAME:VERSION, azureml://.../models/NAME/versions/VERSION, or https://..."
      ;;
  esac
fi

# Resolve the dependency project against the repo so missing metadata is caught
# locally before submission. The path injected into the container stays
# repo-relative because the entry script restores the training/ prefix.
case "$lerobot_project" in
  /*)
    fatal "--lerobot-project must be a repo-relative path (got: $lerobot_project). The value is forwarded into the training container via LEROBOT_PROJECT and resolved against the mounted code snapshot."
    ;;
esac
_project_local="$REPO_ROOT/$lerobot_project"
[[ -f "$_project_local/pyproject.toml" ]] || fatal \
  "--lerobot-project: pyproject.toml not found at $_project_local"
[[ -f "$_project_local/uv.lock" ]] || fatal \
  "--lerobot-project: uv.lock not found at $_project_local. Run 'cd training/vla/lerobot && uv lock' to regenerate."

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Adapter" "${adapter_name}:${adapter_version}"
  print_kv "Adapter Config" "$adapter_config_sha256"
  print_kv "Dependency Extra" "$dependency_extra"
  print_kv "Dataset" "$dataset_repo_id"
  print_kv "Dataset Revision" "${dataset_revision:-<not applicable>}"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Name" "$job_name"
  print_kv "Image" "$image"
  print_kv "Output Dir" "$output_dir"
  print_kv "Training Steps" "${training_steps:-<default>}"
  print_kv "Batch Size" "${batch_size:-<default>}"
  print_kv "Save Freq" "$save_freq"
  print_kv "Log Freq" "${log_freq:-<default>}"
  print_kv "Register Model" "${register_checkpoint:-<none>}"
  print_kv "Init From Model" "${init_from_policy_model:-<none>}"
  print_kv "Init From HF Policy" "${init_from_policy_hf_repo_id:-<none>}"
  print_kv "Init HF Revision" "${init_from_policy_hf_revision:-<none>}"
  if [[ ${#dataset_assets[@]} -gt 0 ]]; then
    print_kv "Data Assets" "${#dataset_assets[@]} asset(s) (ro_mount)"
  fi
  if [[ ${#blob_urls[@]} -gt 0 ]]; then
    print_kv "Blob URLs" "${#blob_urls[@]} dataset(s)"
    print_kv "Dataset Root" "$dataset_root"
  fi
  if [[ ${#dataset_assets[@]} -eq 0 && ${#blob_urls[@]} -eq 0 ]]; then
    print_kv "Data Source" "HuggingFace Hub"
  fi
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Compute" "${compute:-<not set>}"
  print_kv "Instance Type" "$instance_type"
  print_kv "Train Expert Only" "$train_expert_only"
  print_kv "Mixed Precision" "$mixed_precision"
  print_kv "Policy Dtype" "${policy_dtype:-<checkpoint default>}"
  print_kv "Gradient Checkpointing" "$gradient_checkpointing"
  print_kv "Use ImageNet Stats" "$use_imagenet_stats"
  print_kv "Rename Map" "${rename_map:-<none>}"
  print_kv "HF Key Vault" "${hf_key_vault_url:-<none>}"
  print_kv "HF Token Secret" "${hf_token_secret_name:-<none>}"
  print_kv "Environment" "${environment_name}:${environment_version}"
  print_kv "LeRobot Project" "$lerobot_project"
  exit 0
fi

#------------------------------------------------------------------------------
# Register Environment
#------------------------------------------------------------------------------

if [[ "$validate_only" != "true" ]]; then
  register_azureml_environment "$environment_name" "$environment_version" "$image" \
    "$resource_group" "$workspace_name" "$subscription_id"
fi

info "Environment: ${environment_name}:${environment_version}"

if [[ "$assets_only" == "true" ]]; then
  info "Assets prepared; skipping job submission per --assets-only"
  exit 0
fi

managed_identity_client_id=$(resolve_azureml_compute_identity_client_id \
  "$compute" "$resource_group" "$workspace_name")

#------------------------------------------------------------------------------
# Pre-submission Checks
#------------------------------------------------------------------------------

[[ -f "$job_file" ]] || fatal "Job file not found: $job_file"
rendered_job_file=""
resolved_job_file="$job_file"
trap '[[ -n "${rendered_job_file:-}" ]] && rm -f "$rendered_job_file"' EXIT
if [[ -n "$init_from_policy_model" || ${#dataset_assets[@]} -gt 0 ]]; then
  rendered_job_file=$(mktemp "${TMPDIR:-/tmp}/vla-job.XXXXXX")
  mv "$rendered_job_file" "${rendered_job_file}.yml"
  rendered_job_file="${rendered_job_file}.yml"
  if [[ ${#dataset_assets[@]} -gt 0 ]]; then
    render_job_file_with_mounted_inputs "$job_file" "$rendered_job_file" "$init_from_policy_model" "${dataset_assets[@]}"
  else
    render_job_file_with_mounted_inputs "$job_file" "$rendered_job_file" "$init_from_policy_model"
  fi
  resolved_job_file="$rendered_job_file"
fi

#------------------------------------------------------------------------------
# Build Training Command
#
# The AzureML job runs training/il/scripts/lerobot/azureml-train-entry.sh, which
# is uploaded as part of the code asset. The entry script is policy-agnostic;
# The selected adapter resolves policy behavior before this generic lifecycle
# owner points the dependency install at the shared VLA lock.
# Keeping the inline command short avoids multi-line YAML escaping issues with
# the Azure ML K8s extension.
#------------------------------------------------------------------------------

train_cmd="bash il/scripts/lerobot/azureml-train-entry.sh"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

if [[ "$validate_only" == "true" ]]; then
  az_args=(az ml job validate)
else
  az_args=(az ml job create)
fi
az_args+=(
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --file "$resolved_job_file"
  --set "code=$REPO_ROOT/training"
  --set "environment=azureml:${environment_name}:${environment_version}"
)

[[ -n "$compute" ]] && az_args+=(--set "compute=$compute")
[[ -n "$instance_type" ]] && az_args+=(--set "resources.instance_type=$instance_type")
[[ -n "$experiment_name" ]] && az_args+=(--set "experiment_name=$experiment_name")
[[ -n "$display_name" ]] && az_args+=(--set "display_name=$display_name")
[[ -n "$managed_identity_client_id" ]] && az_args+=(--set "environment_variables.AZURE_CLIENT_ID=$managed_identity_client_id")

az_args+=(--set "command=$train_cmd")

# Input values
az_args+=(
  --set "inputs.adapter_name=$adapter_name"
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
  --set "inputs.mixed_precision=$mixed_precision"
)

[[ -n "$policy_repo_id" ]]      && az_args+=(--set "inputs.policy_repo_id=$policy_repo_id")
[[ -n "$dataset_revision" ]]    && az_args+=(--set "inputs.dataset_revision=$dataset_revision")
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
#
# LEROBOT_PROJECT is the VLA-specific opt-in: the entry script defaults to the
# IL project while this submitter selects the shared PI and SmolVLA lock.
az_args+=(
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
  --set "environment_variables.DATASET_REPO_ID=$dataset_repo_id"
  --set "environment_variables.VLA_MODEL_ADAPTER=$adapter_name"
  --set "environment_variables.VLA_ADAPTER_VERSION=$adapter_version"
  --set "environment_variables.VLA_ADAPTER_CONFIG_SHA256=$adapter_config_sha256"
  --set "environment_variables.LEROBOT_POLICY_EXTRA=$dependency_extra"
  --set "environment_variables.POLICY_TYPE=$policy_type"
  --set "environment_variables.JOB_NAME=$job_name"
  --set "environment_variables.OUTPUT_DIR=$output_dir"
  --set "environment_variables.SAVE_FREQ=$save_freq"
  --set "environment_variables.MIXED_PRECISION=$mixed_precision"
  --set "environment_variables.TRAIN_EXPERT_ONLY=$train_expert_only"
  --set "environment_variables.GRADIENT_CHECKPOINTING=$gradient_checkpointing"
  --set "environment_variables.USE_IMAGENET_STATS=$use_imagenet_stats"
  --set "environment_variables.LEROBOT_PROJECT=$lerobot_project"
)

[[ -n "$dataset_revision" ]]    && az_args+=(--set "environment_variables.DATASET_REVISION=$dataset_revision")
[[ -n "$policy_repo_id" ]]      && az_args+=(--set "environment_variables.POLICY_REPO_ID=$policy_repo_id")
[[ -n "$init_from_policy_model" ]] && az_args+=(--set "environment_variables.INIT_FROM_POLICY_MODEL_SOURCE=$init_from_policy_model")
[[ -n "$init_from_policy_hf_repo_id" ]] && az_args+=(--set "environment_variables.INIT_FROM_POLICY_HF_REPO_ID=$init_from_policy_hf_repo_id")
[[ -n "$init_from_policy_hf_revision" ]] && az_args+=(--set "environment_variables.INIT_FROM_POLICY_HF_REVISION=$init_from_policy_hf_revision")
[[ -n "$lerobot_version" ]]     && az_args+=(--set "environment_variables.LEROBOT_VERSION=$lerobot_version")
[[ -n "$policy_dtype" ]]        && az_args+=(--set "environment_variables.POLICY_DTYPE=$policy_dtype")
[[ -n "$training_steps" ]]      && az_args+=(--set "environment_variables.TRAINING_STEPS=$training_steps")
[[ -n "$batch_size" ]]          && az_args+=(--set "environment_variables.BATCH_SIZE=$batch_size")
[[ -n "$eval_freq" ]]           && az_args+=(--set "environment_variables.EVAL_FREQ=$eval_freq")
[[ -n "$log_freq" ]]            && az_args+=(--set "environment_variables.LOG_FREQ=$log_freq")
if [[ -n "$rename_map" ]]; then
  rename_map_b64=$(printf "%s" "$rename_map" | base64 | tr -d "\n")
  az_args+=(--set "environment_variables.RENAME_MAP_B64=$rename_map_b64")
fi
[[ -n "$register_checkpoint" ]] && az_args+=(--set "environment_variables.REGISTER_CHECKPOINT=$register_checkpoint")
[[ -n "$hf_key_vault_url" ]] && az_args+=(--set "environment_variables.HF_KEY_VAULT_URL=$hf_key_vault_url")
[[ -n "$hf_token_secret_name" ]] && az_args+=(--set "environment_variables.HF_TOKEN_SECRET_NAME=$hf_token_secret_name")

if [[ ${#dataset_assets[@]} -gt 0 ]]; then
  dataset_assets_json=$(python3 -c "import json; import sys; print(json.dumps(sys.argv[1:]))" "${dataset_assets[@]}")
  az_args+=(--set "environment_variables.DATASET_ASSETS=$dataset_assets_json")
  az_args+=(--set "environment_variables.DATASET_ASSET_COUNT=${#dataset_assets[@]}")
fi

if [[ ${#blob_urls[@]} -gt 0 ]]; then
  blob_urls_json=$(python3 -c "import json; import sys; print(json.dumps(sys.argv[1:]))" "${blob_urls[@]}")
  az_args+=(--set "environment_variables.BLOB_URLS=$blob_urls_json")
  az_args+=(--set "environment_variables.DATASET_ROOT=$dataset_root")
fi

[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
[[ -n "$save_as" ]] && az_args+=(--save-as "$save_as")
[[ "$validate_only" != "true" ]] && az_args+=(--query "name" -o "tsv")

if [[ "$validate_only" == "true" ]]; then
  info "Validating AzureML LeRobot VLA job for adapter ${adapter_name}:${adapter_version}..."
  "${az_args[@]}"
  section "Validation Summary"
  print_kv "Adapter" "${adapter_name}:${adapter_version}"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Template" "$resolved_job_file"
  print_kv "Status" "Succeeded"
  exit 0
fi

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting AzureML LeRobot VLA training job..."
info "  Adapter: ${adapter_name}:${adapter_version}"
info "  Dataset: $dataset_repo_id"
info "  Policy: $policy_type"
info "  Job Name: $job_name"
info "  Image: $image"
info "  LeRobot Project: $lerobot_project"
[[ ${#blob_urls[@]} -gt 0 ]] && info "  Data Source: Blob URLs (${#blob_urls[@]} dataset(s))"
[[ ${#dataset_assets[@]} -gt 0 ]] && info "  Data Source: AzureML Data Assets (${#dataset_assets[@]} asset(s))"

# Ctrl+C between az invocation and a successful return leaves the operator
# unsure whether the job was accepted. Print a clear pointer to the portal so
# they can resolve the ambiguity instead of blindly resubmitting.
# shellcheck disable=SC2329  # invoked indirectly via `trap`
_interrupt_message() {
  error "Interrupted while waiting for az ml job create. The job may have been submitted."
  error "Check: https://ml.azure.com/runs?wsid=/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.MachineLearningServices/workspaces/${workspace_name}"
  exit 130
}
trap _interrupt_message INT TERM

job_result=$("${az_args[@]}") || fatal \
  "Job submission failed (workspace=${workspace_name}, resource_group=${resource_group}, job_file=${resolved_job_file}). Re-run with '-- --debug' for verbose Azure CLI output."

trap - INT TERM

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
print_kv "Adapter" "${adapter_name}:${adapter_version}"
print_kv "Adapter Config" "$adapter_config_sha256"
print_kv "Dataset" "$dataset_repo_id"
print_kv "Policy Type" "$policy_type"
print_kv "Image" "$image"
print_kv "Compute" "${compute:-<not set>}"
print_kv "Instance Type" "$instance_type"
print_kv "Train Expert Only" "$train_expert_only"
print_kv "Mixed Precision" "$mixed_precision"
print_kv "Policy Dtype" "${policy_dtype:-<checkpoint default>}"
print_kv "Gradient Checkpointing" "$gradient_checkpointing"
print_kv "Rename Map" "${rename_map:-<none>}"
print_kv "Environment" "${environment_name}:${environment_version}"
print_kv "LeRobot Project" "$lerobot_project"
print_kv "Workspace" "$workspace_name"
[[ ${#blob_urls[@]} -gt 0 ]] && print_kv "Blob Datasets" "${#blob_urls[@]}"
[[ ${#dataset_assets[@]} -gt 0 ]] && print_kv "Data Assets" "${#dataset_assets[@]}"
[[ -n "$init_from_policy_model" ]] && print_kv "Init From Model" "$init_from_policy_model"
[[ -n "$init_from_policy_hf_repo_id" ]] && print_kv "Init From HF Policy" "${init_from_policy_hf_repo_id}@${init_from_policy_hf_revision}"
[[ -n "$save_as" ]] && print_kv "Saved Job YAML" "$save_as"
exit 0
