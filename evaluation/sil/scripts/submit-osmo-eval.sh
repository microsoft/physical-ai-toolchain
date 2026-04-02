#!/usr/bin/env bash
#
# OSMO Evaluation Workflow Submission Script
#
# Packages training and evaluation code, encodes the archive, and submits an OSMO
# evaluation workflow using evaluation/sil/workflows/osmo/eval.yaml as a template.
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || cd "$SCRIPT_DIR/../../.." && pwd)

# Source .env file if present (for Azure credentials)
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

# shellcheck source=../../../shared/lib/terraform-outputs.sh
source "${REPO_ROOT}/shared/lib/terraform-outputs.sh"
read_terraform_outputs "${REPO_ROOT}/infrastructure/terraform" 2>/dev/null || true

# shellcheck source=../../../shared/lib/common.sh
source "${REPO_ROOT}/shared/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: submit-osmo-inference.sh [options] [-- osmo-submit-flags]

Packages training and evaluation code, encodes the archive, and submits the evaluation workflow.

Required:
  -c, --checkpoint-uri URI  Checkpoint URI (required). Supported formats:
                            - MLflow: runs:/<run_id>/path or models:/<name>/<version>
                            - Azure Blob: https://<account>.blob.core.windows.net/<container>/<path>
                            - HTTP(S): Direct URL to checkpoint file

Options:
  -w, --workflow PATH       Path to workflow template YAML
  -t, --task NAME           Isaac Lab task name (default: Isaac-Ant-v0)
  -n, --num-envs COUNT      Number of environments (default: 4)
  -m, --max-steps N         Maximum inference steps (default: 500)
  -v, --video-length N      Video length in steps (default: 200)
  -f, --format FORMAT       Inference format: onnx, jit, both (default: both)
  -i, --image IMAGE         Container image override
  -p, --payload-root DIR    Runtime extraction root override

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
  PAYLOAD_ROOT, AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZUREML_WORKSPACE_NAME

Additional arguments after -- are forwarded to osmo workflow submit.

Values are resolved in order: CLI arguments > Environment variables > Terraform outputs
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

if ! command -v base64 >/dev/null 2>&1; then
  echo "base64 utility is required on PATH" >&2
  exit 1
fi

use_local_osmo=false
config_preview=false

WORKFLOW_TEMPLATE=${WORKFLOW_TEMPLATE:-"$REPO_ROOT/workflows/osmo/infer.yaml"}
TMP_DIR=${TMP_DIR:-"$SCRIPT_DIR/.tmp"}
ARCHIVE_PATH=${ARCHIVE_PATH:-"$TMP_DIR/osmo-inference.zip"}
B64_PATH=${B64_PATH:-"$TMP_DIR/osmo-inference.b64"}
TASK_VALUE=${TASK:-Isaac-Ant-v0}
NUM_ENVS_VALUE=${NUM_ENVS:-4}
MAX_STEPS_VALUE=${MAX_STEPS:-500}
VIDEO_LENGTH_VALUE=${VIDEO_LENGTH:-200}
IMAGE_VALUE=${IMAGE:-nvcr.io/nvidia/isaac-lab:2.3.2}
PAYLOAD_ROOT_VALUE=${PAYLOAD_ROOT:-/workspace/isaac_payload}
CHECKPOINT_URI_VALUE=${CHECKPOINT_URI:-}
INFERENCE_FORMAT_VALUE=${INFERENCE_FORMAT:-both}

AZURE_SUBSCRIPTION_ID_VALUE="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
AZURE_RESOURCE_GROUP_VALUE="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
AZURE_WORKSPACE_NAME_VALUE="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
AZURE_STORAGE_ACCOUNT_VALUE="${AZURE_STORAGE_ACCOUNT_NAME:-$(get_storage_account)}"

forward_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--checkpoint-uri)
      CHECKPOINT_URI_VALUE="$2"
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

if [[ -z "$CHECKPOINT_URI_VALUE" ]]; then
  echo "Error: --checkpoint-uri is required" >&2
  usage
  exit 1
fi

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

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Checkpoint URI" "$CHECKPOINT_URI_VALUE"
  print_kv "Task" "$TASK_VALUE"
  print_kv "Num Envs" "$NUM_ENVS_VALUE"
  print_kv "Max Steps" "$MAX_STEPS_VALUE"
  print_kv "Video Length" "$VIDEO_LENGTH_VALUE"
  print_kv "Format" "$INFERENCE_FORMAT_VALUE"
  print_kv "Image" "$IMAGE_VALUE"
  print_kv "Payload Root" "$PAYLOAD_ROOT_VALUE"
  print_kv "Workflow" "$WORKFLOW_TEMPLATE"
  print_kv "Subscription" "${AZURE_SUBSCRIPTION_ID_VALUE:-(not set)}"
  print_kv "Resource Group" "${AZURE_RESOURCE_GROUP_VALUE:-(not set)}"
  print_kv "Workspace" "${AZURE_WORKSPACE_NAME_VALUE:-(not set)}"
  print_kv "Storage Account" "${AZURE_STORAGE_ACCOUNT_VALUE:-(not set)}"
  exit 0
fi

mkdir -p "$TMP_DIR"
rm -f "$ARCHIVE_PATH" "$B64_PATH"

pushd "$REPO_ROOT" >/dev/null
if ! zip -qr "$ARCHIVE_PATH" training/rl evaluation/sil; then
  echo "Failed to create evaluation archive" >&2
  popd >/dev/null
  exit 1
fi
popd >/dev/null

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "Archive not created: $ARCHIVE_PATH" >&2
  exit 1
fi

if base64 --help 2>&1 | grep -q '\-\-input'; then
  base64 --input "$ARCHIVE_PATH" | tr -d '\n' > "$B64_PATH"
else
  base64 -i "$ARCHIVE_PATH" | tr -d '\n' > "$B64_PATH"
fi

if [[ ! -s "$B64_PATH" ]]; then
  echo "Failed to encode archive to base64" >&2
  exit 1
fi

ENCODED_PAYLOAD=$(cat "$B64_PATH")

submit_args=(
  workflow submit "$WORKFLOW_TEMPLATE"
  --set-string "image=$IMAGE_VALUE"
  "encoded_archive=$ENCODED_PAYLOAD"
  "task=$TASK_VALUE"
  "num_envs=$NUM_ENVS_VALUE"
  "max_steps=$MAX_STEPS_VALUE"
  "video_length=$VIDEO_LENGTH_VALUE"
  "payload_root=$PAYLOAD_ROOT_VALUE"
  "checkpoint_uri=$CHECKPOINT_URI_VALUE"
  "inference_format=$INFERENCE_FORMAT_VALUE"
)

if [[ -n "$AZURE_SUBSCRIPTION_ID_VALUE" ]]; then
  submit_args+=("azure_subscription_id=$AZURE_SUBSCRIPTION_ID_VALUE")
fi
if [[ -n "$AZURE_RESOURCE_GROUP_VALUE" ]]; then
  submit_args+=("azure_resource_group=$AZURE_RESOURCE_GROUP_VALUE")
fi
if [[ -n "$AZURE_WORKSPACE_NAME_VALUE" ]]; then
  submit_args+=("azure_workspace_name=$AZURE_WORKSPACE_NAME_VALUE")
fi
if [[ -n "$AZURE_STORAGE_ACCOUNT_VALUE" ]]; then
  submit_args+=("azure_storage_account_name=$AZURE_STORAGE_ACCOUNT_VALUE")
fi

if [[ ${#forward_args[@]} -gt 0 ]]; then
  submit_args+=("${forward_args[@]}")
fi

echo "Submitting inference workflow to OSMO..."
echo "  Checkpoint: $CHECKPOINT_URI_VALUE"
echo "  Task: $TASK_VALUE"
echo "  Format: $INFERENCE_FORMAT_VALUE"
echo "  Azure Subscription: ${AZURE_SUBSCRIPTION_ID_VALUE:-(not set)}"
echo "  Azure Resource Group: ${AZURE_RESOURCE_GROUP_VALUE:-(not set)}"
echo "  Azure ML Workspace: ${AZURE_WORKSPACE_NAME_VALUE:-(not set)}"

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
print_kv "Format" "$INFERENCE_FORMAT_VALUE"
print_kv "Image" "$IMAGE_VALUE"
print_kv "Num Envs" "$NUM_ENVS_VALUE"
print_kv "Subscription" "${AZURE_SUBSCRIPTION_ID_VALUE:-(not set)}"
print_kv "Resource Group" "${AZURE_RESOURCE_GROUP_VALUE:-(not set)}"
print_kv "Workspace" "${AZURE_WORKSPACE_NAME_VALUE:-(not set)}"

echo "Inference workflow submitted successfully"
