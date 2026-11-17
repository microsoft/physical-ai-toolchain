#!/usr/bin/env bash
# Submit a VLM-as-judge workflow to OSMO: the judge scores LeRobot episodes or policy rollout videos on one GPU.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
# shellcheck source=../../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS] [-- osmo-submit-flags]

Submit a VLM-as-judge workflow to OSMO. The judge scores each episode of a LeRobot
dataset, or each rollout video, with an outcome, a confidence, and per-frame progress,
and writes the verdicts as JSONL to the task output.

PLATFORM:
        --profile NAME            Platform profile: azure (default) or local (PLATFORM_PROFILE)

INPUT (one required; a LeRobot dataset brings its episodes and instructions, anything else
is judged as rollout videos):
        --local-dataset NAME      Dataset directory under the profile's datasets root (local profile)
        --input-dataset NAME      OSMO dataset, optionally NAME:VERSION, such as the videos that
                                  submit-osmo-eval.sh --output-dataset publishes
        --input-url URL           Object-store URL that OSMO downloads as the input
        --instruction TEXT        Task the rollouts are judged against (required for videos, which
                                  carry none); overrides a LeRobot dataset's own instructions
        --limit N                 Judge at most N episodes or rollouts
        --indices SPEC            LeRobot episode indices, such as 0,3,5 or 0-9

JUDGE:
        --backend NAME            qwen3-vl (default), openai-compat, or echo
        --model-id ID             Hub model, or a model directory in the task given as an absolute
                                  path (default: $DEFAULT_VLM_JUDGE_MODEL)
        --model-revision SHA      Hugging Face commit SHA that pins the model (default: pinned for
                                  the default model; required for another Hub model; omitted for
                                  a model directory)
        --base-url URL            OpenAI-compatible endpoint (openai-compat backend)
        --n-frames N              Frames sampled per episode (default: 12)

OUTPUT:
        --output-dataset NAME     Publish the verdicts as a version of this OSMO dataset
        --dataset-bucket NAME     OSMO bucket for dataset inputs and outputs (default: training)

RESOURCES (defaults come from the platform profile, then these values):
        --gpu COUNT               Number of GPUs (default: 1)
        --cpu COUNT               CPU cores (default: 8)
        --memory SIZE             Memory with unit (default: 32Gi)
        --storage SIZE            Storage with unit (default: 60Gi)

OTHER:
    -i, --image IMAGE             Container image (default: DEFAULT_LEROBOT_EVAL_IMAGE)
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

Additional arguments after -- are forwarded to osmo workflow submit.

EXAMPLES:
    # Judge the episodes of a local dataset with the pinned Qwen3-VL-4B
    $(basename "$0") --profile local --local-dataset so101-learner

    # Judge the rollout videos an Isaac Lab evaluation published
    $(basename "$0") --profile local --input-dataset cartpole-eval \\
      --instruction "Keep the pole balanced upright"
EOF
}

workflow="$SCRIPT_DIR/../workflows/osmo/judge.yaml"
profile=""
local_dataset=""
input_dataset=""
input_url=""
instruction=""
limit=""
indices=""
backend="${VLM_JUDGE_BACKEND:-qwen3-vl}"
model_id=""
model_revision=""
base_url=""
n_frames="12"
output_dataset=""
dataset_bucket="training"
image="${IMAGE:-$DEFAULT_LEROBOT_EVAL_IMAGE}"
gpu="${OSMO_GPU:-}"
cpu="${OSMO_CPU:-}"
memory="${OSMO_MEMORY:-}"
storage="${OSMO_STORAGE:-}"
config_preview=false
forward_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    --profile)            profile="$2"; shift 2 ;;
    --local-dataset)      local_dataset="$2"; shift 2 ;;
    --input-dataset)      input_dataset="$2"; shift 2 ;;
    --input-url)          input_url="$2"; shift 2 ;;
    --instruction)        instruction="$2"; shift 2 ;;
    --limit)              limit="$2"; shift 2 ;;
    --indices)            indices="$2"; shift 2 ;;
    --backend)            backend="$2"; shift 2 ;;
    --model-id)           model_id="$2"; shift 2 ;;
    --model-revision)     model_revision="$2"; shift 2 ;;
    --base-url)           base_url="$2"; shift 2 ;;
    --n-frames)           n_frames="$2"; shift 2 ;;
    --output-dataset)     output_dataset="$2"; shift 2 ;;
    --dataset-bucket)     dataset_bucket="$2"; shift 2 ;;
    -i|--image)           image="$2"; shift 2 ;;
    --gpu)                gpu="$2"; shift 2 ;;
    --cpu)                cpu="$2"; shift 2 ;;
    --memory)             memory="$2"; shift 2 ;;
    --storage)            storage="$2"; shift 2 ;;
    --config-preview)     config_preview=true; shift ;;
    --)                   shift; forward_args=("$@"); break ;;
    *)                    fatal "Unknown option: $1 (use --help)" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools osmo zip python3

load_platform_profile "$profile"
gpu="${gpu:-${PROFILE_TASK_GPU:-1}}"
cpu="${cpu:-${PROFILE_TASK_CPU:-8}}"
memory="${memory:-${PROFILE_TASK_MEMORY:-32Gi}}"
storage="${storage:-${PROFILE_TASK_STORAGE:-60Gi}}"

inputs=0
[[ -n "$local_dataset" ]] && inputs=$((inputs + 1))
[[ -n "$input_dataset" ]] && inputs=$((inputs + 1))
[[ -n "$input_url" ]] && inputs=$((inputs + 1))
(( inputs == 1 )) || fatal "Choose exactly one input: --local-dataset, --input-dataset, or --input-url"

input_dir=""
input_summary=""
if [[ -n "$local_dataset" ]]; then
  input_dir="$(platform_local_dataset_path "$local_dataset")"
  input_summary="local dataset $input_dir"
elif [[ -n "$input_dataset" ]]; then
  input_summary="OSMO dataset $dataset_bucket/$input_dataset"
else
  input_summary="$input_url"
fi

case "$backend" in
  qwen3-vl|echo) ;;
  openai-compat) [[ -n "$base_url" ]] || fatal "--backend openai-compat needs --base-url" ;;
  *) fatal "Unsupported backend: $backend (use qwen3-vl, openai-compat, or echo)" ;;
esac
model_id="${model_id:-$DEFAULT_VLM_JUDGE_MODEL}"
if [[ "$backend" == "qwen3-vl" ]]; then
  if [[ "$model_id" == "$DEFAULT_VLM_JUDGE_MODEL" ]]; then
    model_revision="${model_revision:-$DEFAULT_VLM_JUDGE_MODEL_REVISION}"
  fi
  # The judge loads an absolute, ./, or ../ model path as a local directory and rejects a revision for it.
  if [[ "$model_id" == /* || "$model_id" == ./* || "$model_id" == ../* ]]; then
    [[ -z "$model_revision" ]] || fatal "--model-revision does not apply to the model directory $model_id; omit it"
  else
    require_hf_pin "$model_id" "$model_revision" "--model-revision"
  fi
fi
[[ "$n_frames" =~ ^[1-9][0-9]*$ ]] || fatal "--n-frames must be a positive integer (got: $n_frames)"
[[ -z "$limit" || "$limit" =~ ^[1-9][0-9]*$ ]] || fatal "--limit must be a positive integer (got: $limit)"
[[ -f "$workflow" ]] || fatal "Workflow template not found: $workflow"
require_platform_code_storage

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_platform_profile_preview
  print_kv "OSMO Platform" "$PROFILE_OSMO_GPU_PLATFORM"
  print_kv "Input" "$input_summary"
  print_kv "Instruction" "${instruction:-<from the dataset>}"
  print_kv "Backend" "$backend"
  print_kv "Model" "$model_id${model_revision:+@$model_revision}"
  print_kv "Frames" "$n_frames"
  print_kv "Limit" "${limit:-<all>}"
  print_kv "Output Dataset" "${output_dataset:+$dataset_bucket/$output_dataset}"
  print_kv "Image" "$image"
  print_kv "GPU" "$gpu"
  print_kv "CPU" "$cpu"
  print_kv "Memory" "$memory"
  print_kv "Storage" "$storage"
  exit 0
fi

use_platform_osmo_login
# The pinned model is public; a credential, when the service holds one, also opens private models.
hub_source=false
[[ "$backend" == "qwen3-vl" ]] && hub_source=true
use_huggingface_credential="$(platform_huggingface_credential "$hub_source")"

#------------------------------------------------------------------------------
# Package and Submit
#------------------------------------------------------------------------------

entry_script="$SCRIPT_DIR/osmo-judge-entry.sh"
[[ -f "$entry_script" ]] || fatal "Entry script not found: $entry_script"
entry_script_b64="$(base64 < "$entry_script" | tr -d '\n')"
# The instruction travels base64-encoded so any text survives the workflow's YAML template.
instruction_b64="$(printf '%s' "$instruction" | base64 | tr -d '\n')"

info "Packaging and uploading the judge payload..."
code_url=$(cd "$REPO_ROOT" && stage_and_upload_code "$REPO_ROOT" "$PROFILE_OSMO_CODE_STORAGE_URL" \
  pyproject.toml uv.lock \
  evaluation/vlm_judge/pyproject.toml evaluation/vlm_judge/*.py \
  data-management/dataset-io/pyproject.toml data-management/dataset-io/physical_ai_dataset_io/*.py \
  data-management/curation/pyproject.toml data-management/viewer/backend/pyproject.toml) \
  || fatal "Failed to stage and upload the judge payload"

submit_args=(
  workflow submit "$workflow"
  --set-string "image=$image"
  "code_url=$code_url"
  "entry_script_b64=$entry_script_b64"
  "input_dir=$input_dir"
  "input_dataset=$input_dataset"
  "input_url=$input_url"
  "dataset_bucket=$dataset_bucket"
  "output_dataset=$output_dataset"
  "backend=$backend"
  "model_id=$model_id"
  "model_revision=$model_revision"
  "base_url=$base_url"
  "n_frames=$n_frames"
  "limit=$limit"
  "indices=$indices"
  "instruction_b64=$instruction_b64"
  "use_huggingface_credential=$use_huggingface_credential"
  "gpu=$gpu"
  "cpu=$cpu"
  "memory=$memory"
  "storage=$storage"
  "platform=$PROFILE_OSMO_GPU_PLATFORM"
)
[[ ${#forward_args[@]} -gt 0 ]] && submit_args+=("${forward_args[@]}")

info "Submitting the VLM judge workflow to OSMO..."
osmo "${submit_args[@]}" || fatal "Failed to submit workflow"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Platform Profile" "$PROFILE_NAME"
print_kv "Input" "$input_summary"
print_kv "Backend" "$backend"
print_kv "Model" "$model_id${model_revision:+@$model_revision}"
print_kv "Output Dataset" "${output_dataset:+$dataset_bucket/$output_dataset}"
info "Workflow submitted successfully"
