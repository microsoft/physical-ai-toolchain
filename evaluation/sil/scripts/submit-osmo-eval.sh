#!/usr/bin/env bash
#
# OSMO Evaluation Workflow Submission Script
#
# Packages training and evaluation code, encodes the archive, and submits an OSMO
# evaluation workflow using evaluation/sil/workflows/osmo/eval.yaml as a template.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))

# Source .env file if present (for Azure credentials)
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "${REPO_ROOT}/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "${REPO_ROOT}/infrastructure/terraform" 2>/dev/null || true

# shellcheck source=../../../scripts/lib/common.sh
source "${REPO_ROOT}/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/platform-profile.sh
source "${REPO_ROOT}/scripts/lib/platform-profile.sh"

usage() {
  cat << EOF
Usage: submit-osmo-eval.sh [options] [-- osmo-submit-flags]

Packages training and evaluation code, encodes the archive, and submits the evaluation workflow.
The workflow gates the policy on its success rate, then exports it and replays it with video.
It fails when the success rate is below the threshold.

Policy (one is required):
  -c, --checkpoint-uri URI  Checkpoint to evaluate. Supported formats:
                            - MLflow: runs:/<run_id>/path or models:/<name>/<version>
                            - Azure Blob: https://<account>.blob.core.windows.net/<container>/<path>
                            - HTTP(S): Direct URL to checkpoint file
                            - Container path: an absolute path in the task, such as a
                              dataset under /mnt/datasets on the local profile
      --policy-image IMAGE  Runnable policy carrier from fleet-deployment/setup/build-model-image.sh;
                            runs as the task image and evaluates /policy unless
                            --checkpoint-uri names another path

Gate:
      --framework NAME      Training framework of the checkpoint: skrl or rsl_rl (default: skrl)
      --eval-episodes N     Episodes the success gate evaluates (default: 100)
      --success-threshold F Success rate required to pass (default: the evaluator's 0.7)

Outputs:
      --output-dataset NAME Publish the videos and metrics as a version of this OSMO dataset
      --dataset-bucket NAME OSMO bucket for --output-dataset (default: training)
      --experiment-name NAME
                            MLflow experiment for the evaluation run (default: inference-<task>)

Options:
      --profile NAME        Platform profile: azure (default) or local (PLATFORM_PROFILE)
  -w, --workflow PATH       Path to workflow template YAML
  -t, --task NAME           Isaac Lab task name (default: Isaac-Ant-v0)
  -n, --num-envs COUNT      Number of environments (default: 4)
  -m, --max-steps N         Maximum inference steps (default: 500)
  -v, --video-length N      Video length in steps (default: 200)
  -f, --format FORMAT       Inference format: onnx, jit, both (default: both)
  -i, --image IMAGE         Container image override (default: ${DEFAULT_ISAAC_LAB_IMAGE})
  -p, --payload-root DIR    Runtime extraction root override

Resources (defaults come from the platform profile, then these values):
      --gpu COUNT           Number of GPUs (default: 1)
      --cpu COUNT           CPU cores (default: 20)
      --memory SIZE         Memory with unit (default: 180Gi)
      --storage SIZE        Storage with unit (default: 100Gi)

Azure context overrides (resolved from Terraform outputs if not provided):
      --azure-subscription-id ID    Azure subscription ID
      --azure-resource-group NAME   Azure resource group
      --azure-workspace-name NAME   Azure ML workspace name

General:
      --use-local-osmo        Use local osmo-dev CLI instead of production osmo
      --config-preview        Print configuration and exit
  -h, --help              Show this help message and exit

Environment overrides:
  TASK, NUM_ENVS, MAX_STEPS, VIDEO_LENGTH, IMAGE, CHECKPOINT_URI, INFERENCE_FORMAT
  FRAMEWORK, EVAL_EPISODES, SUCCESS_THRESHOLD, EXPERIMENT_NAME
  PAYLOAD_ROOT, AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZUREML_WORKSPACE_NAME
  PLATFORM_PROFILE, TRACKING_BACKEND (azureml, mlflow, or none), OSMO_GPU, OSMO_CPU, OSMO_MEMORY, OSMO_STORAGE

Additional arguments after -- are forwarded to osmo workflow submit.

Values are resolved in order: CLI arguments > Environment variables > Platform profile > Terraform outputs
EOF
}

if ! command -v osmo >/dev/null 2>&1; then
  echo "osmo CLI is required on PATH" >&2
  exit 1
fi

if ! command -v zip >/dev/null 2>&1; then
  echo "zip utility is required on PATH" >&2
  exit 1
fi

use_local_osmo=false
config_preview=false

WORKFLOW_TEMPLATE=${WORKFLOW_TEMPLATE:-"$REPO_ROOT/evaluation/sil/workflows/osmo/eval.yaml"}
TASK_VALUE=${TASK:-Isaac-Ant-v0}
NUM_ENVS_VALUE=${NUM_ENVS:-4}
MAX_STEPS_VALUE=${MAX_STEPS:-500}
VIDEO_LENGTH_VALUE=${VIDEO_LENGTH:-200}
IMAGE_VALUE=${IMAGE:-$DEFAULT_ISAAC_LAB_IMAGE}
PAYLOAD_ROOT_VALUE=${PAYLOAD_ROOT:-/tmp/isaac_payload}
CHECKPOINT_URI_VALUE=${CHECKPOINT_URI:-}
INFERENCE_FORMAT_VALUE=${INFERENCE_FORMAT:-both}
POLICY_IMAGE_VALUE=""
FRAMEWORK_VALUE=${FRAMEWORK:-skrl}
EVAL_EPISODES_VALUE=${EVAL_EPISODES:-100}
SUCCESS_THRESHOLD_VALUE=${SUCCESS_THRESHOLD:--1}
OUTPUT_DATASET_VALUE=""
DATASET_BUCKET_VALUE="training"
EXPERIMENT_NAME_VALUE=${EXPERIMENT_NAME:-}

AZURE_SUBSCRIPTION_ID_VALUE="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
AZURE_RESOURCE_GROUP_VALUE="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
AZURE_WORKSPACE_NAME_VALUE="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
AZURE_STORAGE_ACCOUNT_VALUE="${AZURE_STORAGE_ACCOUNT_NAME:-$(get_storage_account)}"
GPU_VALUE="${OSMO_GPU:-}"
CPU_VALUE="${OSMO_CPU:-}"
MEMORY_VALUE="${OSMO_MEMORY:-}"
STORAGE_VALUE="${OSMO_STORAGE:-}"
PROFILE_VALUE=""

forward_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--checkpoint-uri)
      CHECKPOINT_URI_VALUE="$2"
      shift 2
      ;;
    --profile)
      PROFILE_VALUE="$2"
      shift 2
      ;;
    --policy-image)
      POLICY_IMAGE_VALUE="$2"
      shift 2
      ;;
    --framework)
      FRAMEWORK_VALUE="$2"
      shift 2
      ;;
    --eval-episodes)
      EVAL_EPISODES_VALUE="$2"
      shift 2
      ;;
    --success-threshold)
      SUCCESS_THRESHOLD_VALUE="$2"
      shift 2
      ;;
    --output-dataset)
      OUTPUT_DATASET_VALUE="$2"
      shift 2
      ;;
    --dataset-bucket)
      DATASET_BUCKET_VALUE="$2"
      shift 2
      ;;
    --experiment-name)
      EXPERIMENT_NAME_VALUE="$2"
      shift 2
      ;;
    --gpu)
      GPU_VALUE="$2"
      shift 2
      ;;
    --cpu)
      CPU_VALUE="$2"
      shift 2
      ;;
    --memory)
      MEMORY_VALUE="$2"
      shift 2
      ;;
    --storage)
      STORAGE_VALUE="$2"
      shift 2
      ;;
    -w|--workflow)
      WORKFLOW_TEMPLATE="$2"
      shift 2
      ;;
    -t|--task)
      TASK_VALUE="$2"
      shift 2
      ;;
    -n|--num-envs)
      NUM_ENVS_VALUE="$2"
      shift 2
      ;;
    -m|--max-steps)
      MAX_STEPS_VALUE="$2"
      shift 2
      ;;
    -v|--video-length)
      VIDEO_LENGTH_VALUE="$2"
      shift 2
      ;;
    -f|--format)
      INFERENCE_FORMAT_VALUE="$2"
      shift 2
      ;;
    -i|--image)
      IMAGE_VALUE="$2"
      shift 2
      ;;
    -p|--payload-root)
      PAYLOAD_ROOT_VALUE="$2"
      shift 2
      ;;
    --azure-subscription-id)
      AZURE_SUBSCRIPTION_ID_VALUE="$2"
      shift 2
      ;;
    --azure-resource-group)
      AZURE_RESOURCE_GROUP_VALUE="$2"
      shift 2
      ;;
    --azure-workspace-name)
      AZURE_WORKSPACE_NAME_VALUE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --use-local-osmo)
      use_local_osmo=true
      shift
      ;;
    --config-preview)
      config_preview=true
      shift
      ;;
    --)
      shift
      forward_args+=("$@")
      break
      ;;
    *)
      forward_args+=("$1")
      shift
      ;;
  esac
done

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

load_platform_profile "$PROFILE_VALUE"
GPU_VALUE="${GPU_VALUE:-${PROFILE_TASK_GPU:-1}}"
CPU_VALUE="${CPU_VALUE:-${PROFILE_TASK_CPU:-20}}"
MEMORY_VALUE="${MEMORY_VALUE:-${PROFILE_TASK_MEMORY:-180Gi}}"
STORAGE_VALUE="${STORAGE_VALUE:-${PROFILE_TASK_STORAGE:-100Gi}}"

# A runnable carrier is the task image and holds the policy at /policy.
if [[ -n "$POLICY_IMAGE_VALUE" ]]; then
  IMAGE_VALUE="$POLICY_IMAGE_VALUE"
  CHECKPOINT_URI_VALUE="${CHECKPOINT_URI_VALUE:-/policy}"
fi

if [[ -z "$CHECKPOINT_URI_VALUE" ]]; then
  echo "Error: --checkpoint-uri or --policy-image is required" >&2
  usage
  exit 1
fi

case "$FRAMEWORK_VALUE" in
  skrl|rsl_rl) ;;
  *) fatal "Unsupported framework: $FRAMEWORK_VALUE (use: skrl, rsl_rl)" ;;
esac
[[ "$EVAL_EPISODES_VALUE" =~ ^[1-9][0-9]*$ ]] || fatal "--eval-episodes must be a positive integer (got: $EVAL_EPISODES_VALUE)"
[[ "$SUCCESS_THRESHOLD_VALUE" =~ ^-?[0-9]*\.?[0-9]+$ ]] || fatal "--success-threshold must be a number (got: $SUCCESS_THRESHOLD_VALUE)"

if [[ ! -f "$WORKFLOW_TEMPLATE" ]]; then
  echo "Workflow template not found: $WORKFLOW_TEMPLATE" >&2
  exit 1
fi

if [[ ! -d "$REPO_ROOT/training/rl" ]]; then
  echo "Directory training/rl not found under $REPO_ROOT" >&2
  exit 1
fi

if [[ ! -f "$REPO_ROOT/training/packaging/scripts/export_policy.py" ]]; then
  echo "Export script training/packaging/scripts/export_policy.py not found under $REPO_ROOT" >&2
  exit 1
fi

entry_script="$SCRIPT_DIR/osmo-eval-entry.sh"
if [[ ! -f "$entry_script" ]]; then
  echo "Entry script not found: $entry_script" >&2
  exit 1
fi

entry_script_b64="$(base64 < "$entry_script" | tr -d '\n')"

if [[ "$PROFILE_TRACKING_BACKEND" == "none" && "$CHECKPOINT_URI_VALUE" =~ ^(runs|models):/ ]]; then
  fatal "Checkpoint $CHECKPOINT_URI_VALUE needs a tracking backend; TRACKING_BACKEND is none"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_platform_profile_preview
  print_kv "OSMO Platform" "$PROFILE_OSMO_GPU_PLATFORM"
  print_kv "Checkpoint URI" "$CHECKPOINT_URI_VALUE"
  [[ -n "$POLICY_IMAGE_VALUE" ]] && print_kv "Policy Image" "$POLICY_IMAGE_VALUE (task image)"
  print_kv "Framework" "$FRAMEWORK_VALUE"
  print_kv "Eval Episodes" "$EVAL_EPISODES_VALUE"
  print_kv "Success Threshold" "$([[ "$SUCCESS_THRESHOLD_VALUE" == -* ]] && echo "evaluator default" || echo "$SUCCESS_THRESHOLD_VALUE")"
  print_kv "Output Dataset" "${OUTPUT_DATASET_VALUE:+$DATASET_BUCKET_VALUE/$OUTPUT_DATASET_VALUE}"
  print_kv "Experiment" "${EXPERIMENT_NAME_VALUE:-inference-$TASK_VALUE}"
  print_kv "Task" "$TASK_VALUE"
  print_kv "Num Envs" "$NUM_ENVS_VALUE"
  print_kv "Max Steps" "$MAX_STEPS_VALUE"
  print_kv "Video Length" "$VIDEO_LENGTH_VALUE"
  print_kv "Format" "$INFERENCE_FORMAT_VALUE"
  print_kv "Image" "$IMAGE_VALUE"
  print_kv "Payload Root" "$PAYLOAD_ROOT_VALUE"
  print_kv "GPU" "$GPU_VALUE"
  print_kv "CPU" "$CPU_VALUE"
  print_kv "Memory" "$MEMORY_VALUE"
  print_kv "Storage" "$STORAGE_VALUE"
  print_kv "Workflow" "$WORKFLOW_TEMPLATE"
  if [[ "$PROFILE_TRACKING_BACKEND" == "azureml" ]]; then
    print_kv "Subscription" "${AZURE_SUBSCRIPTION_ID_VALUE:-(not set)}"
    print_kv "Resource Group" "${AZURE_RESOURCE_GROUP_VALUE:-(not set)}"
    print_kv "Workspace" "${AZURE_WORKSPACE_NAME_VALUE:-(not set)}"
  fi
  [[ "$PROFILE_NAME" == "azure" ]] && print_kv "Storage Account" "${AZURE_STORAGE_ACCOUNT_VALUE:-(not set)}"
  exit 0
fi

require_platform_code_storage
use_platform_osmo_login

CODE_URL=$(stage_and_upload_code "$REPO_ROOT" \
  "$PROFILE_OSMO_CODE_STORAGE_URL" \
  training/__init__.py training/rl training/packaging training/utils evaluation/sil evaluation/metrics) \
  || fatal "Failed to stage and upload evaluation payload"

submit_args=(
  workflow submit "$WORKFLOW_TEMPLATE"
  --set-string "image=$IMAGE_VALUE"
  "code_url=$CODE_URL"
  "entry_script_b64=$entry_script_b64"
  "task=$TASK_VALUE"
  "num_envs=$NUM_ENVS_VALUE"
  "max_steps=$MAX_STEPS_VALUE"
  "video_length=$VIDEO_LENGTH_VALUE"
  "payload_root=$PAYLOAD_ROOT_VALUE"
  "checkpoint_uri=$CHECKPOINT_URI_VALUE"
  "inference_format=$INFERENCE_FORMAT_VALUE"
  "framework=$FRAMEWORK_VALUE"
  "eval_episodes=$EVAL_EPISODES_VALUE"
  "success_threshold=$SUCCESS_THRESHOLD_VALUE"
  "output_dataset=$OUTPUT_DATASET_VALUE"
  "dataset_bucket=$DATASET_BUCKET_VALUE"
  "experiment_name=$EXPERIMENT_NAME_VALUE"
  "gpu=$GPU_VALUE"
  "cpu=$CPU_VALUE"
  "memory=$MEMORY_VALUE"
  "storage=$STORAGE_VALUE"
  "platform=$PROFILE_OSMO_GPU_PLATFORM"
)
mapfile -t tracking_values < <(platform_tracking_workflow_values)
submit_args+=("${tracking_values[@]}")

# Only AzureML tracking reads the workspace context; Blob uploads use the Azure profile's storage account.
if [[ "$PROFILE_TRACKING_BACKEND" == "azureml" ]]; then
  if [[ -n "$AZURE_SUBSCRIPTION_ID_VALUE" ]]; then
    submit_args+=("azure_subscription_id=$AZURE_SUBSCRIPTION_ID_VALUE")
  fi
  if [[ -n "$AZURE_RESOURCE_GROUP_VALUE" ]]; then
    submit_args+=("azure_resource_group=$AZURE_RESOURCE_GROUP_VALUE")
  fi
  if [[ -n "$AZURE_WORKSPACE_NAME_VALUE" ]]; then
    submit_args+=("azure_workspace_name=$AZURE_WORKSPACE_NAME_VALUE")
  fi
fi
if [[ "$PROFILE_NAME" == "azure" && -n "$AZURE_STORAGE_ACCOUNT_VALUE" ]]; then
  submit_args+=("azure_storage_account_name=$AZURE_STORAGE_ACCOUNT_VALUE")
fi

if [[ ${#forward_args[@]} -gt 0 ]]; then
  submit_args+=("${forward_args[@]}")
fi

echo "Submitting evaluation workflow to OSMO..."
echo "  Checkpoint: $CHECKPOINT_URI_VALUE"
echo "  Task: $TASK_VALUE"
echo "  Format: $INFERENCE_FORMAT_VALUE"
echo "  Profile: $PROFILE_NAME"
echo "  Tracking: $PROFILE_TRACKING_BACKEND"

if ! osmo "${submit_args[@]}"; then
  echo "Failed to submit workflow to OSMO" >&2
  exit 1
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Checkpoint" "$CHECKPOINT_URI_VALUE"
print_kv "Task" "$TASK_VALUE"
print_kv "Gate" "$FRAMEWORK_VALUE, $EVAL_EPISODES_VALUE episodes"
print_kv "Format" "$INFERENCE_FORMAT_VALUE"
print_kv "Image" "$IMAGE_VALUE"
print_kv "Num Envs" "$NUM_ENVS_VALUE"
print_kv "Platform Profile" "$PROFILE_NAME"
print_kv "Tracking Backend" "$PROFILE_TRACKING_BACKEND"
if [[ "$PROFILE_TRACKING_BACKEND" == "azureml" ]]; then
  print_kv "Subscription" "${AZURE_SUBSCRIPTION_ID_VALUE:-(not set)}"
  print_kv "Resource Group" "${AZURE_RESOURCE_GROUP_VALUE:-(not set)}"
  print_kv "Workspace" "${AZURE_WORKSPACE_NAME_VALUE:-(not set)}"
fi

echo "Inference workflow submitted successfully"
