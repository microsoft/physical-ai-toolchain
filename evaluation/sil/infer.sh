#!/bin/bash
# Inference entry point script for OSMO workflow.
# Supports ONNX, JIT, or both inference formats via --inference-format flag.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFERENCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC_DIR="$(cd "${INFERENCE_DIR}/.." && pwd)"

ENV_FILE="${INFERENCE_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

declare -a python_cmd
if [[ -n "${PYTHON:-}" ]]; then
  IFS=' ' read -r -a python_cmd <<< "${PYTHON}"
else
  python_cmd=(python)
fi

python_exec="/isaac-sim/kit/python/bin/python3"
if [[ ! -x "${python_exec}" ]]; then
  python_exec="${python_cmd[0]}"
fi

# run_python uses raw Python for pip/dependency operations
run_python() {
  if [[ -n "${python_exec}" ]]; then
    "${python_exec}" "$@"
  else
    "${python_cmd[@]}" "$@"
  fi
}

# run_isaaclab uses isaaclab.sh -p for simulation scripts that need full env
run_isaaclab() {
  "${python_cmd[@]}" "$@"
}

TASK="${TASK:-Isaac-Ant-v0}"
NUM_ENVS="${NUM_ENVS:-4}"
MAX_STEPS="${MAX_STEPS:-500}"
VIDEO_LENGTH="${VIDEO_LENGTH:-200}"
INFERENCE_FORMAT="${INFERENCE_FORMAT:-both}"
CHECKPOINT_URI="${CHECKPOINT_URI:-}"
CHECKPOINT_SHA256="${CHECKPOINT_SHA256:-}"
EXPORT_DIR=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --task              Task name (default: Isaac-Ant-v0)
  --num-envs          Number of environments (default: 4)
  --max-steps         Maximum simulation steps (default: 500)
  --video-length      Video recording length in steps (default: 200)
  --inference-format  Inference format: onnx, jit, or both (default: both)
  --checkpoint-uri    URI to checkpoint (.pt file)
  --checkpoint-sha256  Expected SHA256 hash for checkpoint verification
  --headless          Run in headless mode
  -h, --help          Show this help message

Environment Variables:
  TASK, NUM_ENVS, MAX_STEPS, VIDEO_LENGTH, INFERENCE_FORMAT, CHECKPOINT_URI, CHECKPOINT_SHA256
  PYTHON               Python command (default: python)
EOF
  exit 0
}

HEADLESS=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task)
      TASK="$2"
      shift 2
      ;;
    --num-envs|--num_envs)
      NUM_ENVS="$2"
      shift 2
      ;;
    --max-steps|--max_steps)
      MAX_STEPS="$2"
      shift 2
      ;;
    --video-length|--video_length)
      VIDEO_LENGTH="$2"
      shift 2
      ;;
    --inference-format|--inference_format)
      INFERENCE_FORMAT="$2"
      shift 2
      ;;
    --checkpoint-uri|--checkpoint_uri)
      CHECKPOINT_URI="$2"
      shift 2
      ;;
    --checkpoint-sha256|--checkpoint_sha256)
      CHECKPOINT_SHA256="$2"
      shift 2
      ;;
    --headless)
      HEADLESS="--headless"
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "${CHECKPOINT_URI}" ]]; then
  echo "Error: --checkpoint-uri is required" >&2
  exit 1
fi

prebundle_path="/isaac-sim/exts/omni.pip.compute/pip_prebundle"
if [[ -d "${prebundle_path}" ]]; then
  export PYTHONPATH="${prebundle_path}:${SRC_DIR}:${PYTHONPATH:-}"
else
  export PYTHONPATH="${SRC_DIR}:${PYTHONPATH:-}"
fi

INFERENCE_MANIFEST="${INFERENCE_DIR}/pyproject.toml"
INFERENCE_REQS="$(mktemp)"
cleanup() {
  rm -rf "${CHECKPOINT_DIR:-}" "${INFERENCE_REQS}"
}
trap cleanup EXIT

if command -v uv &>/dev/null; then
  echo "Installing inference workflow dependencies from manifest..."
  uv pip compile "${INFERENCE_MANIFEST}" -o "${INFERENCE_REQS}"
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    uv pip install --no-cache-dir --requirement "${INFERENCE_REQS}" || \
      uv pip install --no-cache-dir --requirement "${INFERENCE_REQS}" --index-strategy first-index \
        --extra-index-url https://download.pytorch.org/whl/cu124
  else
    uv pip install --no-cache-dir --system --requirement "${INFERENCE_REQS}" || \
      uv pip install --no-cache-dir --system --requirement "${INFERENCE_REQS}" --index-strategy first-index \
        --extra-index-url https://download.pytorch.org/whl/cu124
  fi
else
  echo "Error: uv is required to compile workflow manifest dependencies" >&2
  exit 1
fi

CHECKPOINT_DIR=$(mktemp -d)
echo "=============================================="
echo "Downloading checkpoint from: ${CHECKPOINT_URI}"
echo "=============================================="

BLOB_STORAGE_ACCOUNT=""
BLOB_CONTAINER=""
download_checkpoint() {
  local uri="$1"
  local dst_dir="$2"

  if [[ "${uri}" == runs:/* ]] || [[ "${uri}" == models:/* ]]; then
    echo "Detected MLflow artifact URI"
    run_python << MLFLOW_DOWNLOAD
import os
import mlflow

sub_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
rg = os.environ.get("AZURE_RESOURCE_GROUP", "")
ws = os.environ.get("AZUREML_WORKSPACE_NAME", "")

if sub_id and rg and ws:
    tracking_uri = (
        f"azureml://westus3.api.azureml.ms/mlflow/v1.0/subscriptions/{sub_id}"
        f"/resourceGroups/{rg}/providers/Microsoft.MachineLearningServices"
        f"/workspaces/{ws}"
    )
    print(f"Setting MLflow tracking URI: {tracking_uri}")
    mlflow.set_tracking_uri(tracking_uri)
else:
    print("Warning: Azure ML environment variables not set, using default MLflow tracking")

local_path = mlflow.artifacts.download_artifacts(artifact_uri="${uri}", dst_path="${dst_dir}")
print(f"Downloaded to: {local_path}")
MLFLOW_DOWNLOAD
  elif [[ "${uri}" == https://*.blob.core.windows.net/* ]]; then
    echo "Detected Azure Blob Storage URL"
    BLOB_STORAGE_ACCOUNT=$(echo "${uri}" | sed -n 's|https://\([^.]*\)\.blob\.core\.windows\.net/.*|\1|p')
    BLOB_CONTAINER=$(echo "${uri}" | sed -n 's|https://[^/]*/\([^/]*\)/.*|\1|p')
    run_python << BLOB_DOWNLOAD
import os
from pathlib import Path
from urllib.parse import urlparse
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

uri = "${uri}"
dst_dir = "${dst_dir}"

parsed = urlparse(uri)
account = parsed.netloc.split('.')[0]
path_parts = parsed.path.lstrip('/').split('/', 1)
container = path_parts[0]
blob_path = path_parts[1] if len(path_parts) > 1 else ''

credential = DefaultAzureCredential()
blob_service = BlobServiceClient(
    account_url=f"https://{account}.blob.core.windows.net",
    credential=credential
)

blob_client = blob_service.get_blob_client(container=container, blob=blob_path)
local_file = Path(dst_dir) / Path(blob_path).name
local_file.parent.mkdir(parents=True, exist_ok=True)

print(f"Downloading to: {local_file}")
with open(local_file, "wb") as f:
    stream = blob_client.download_blob()
    f.write(stream.readall())
print(f"Downloaded: {local_file}")
BLOB_DOWNLOAD
  else
    echo "Detected HTTP URL, using curl"
    local filename
    filename=$(basename "${uri}")
    curl -fsSL -o "${dst_dir}/${filename}" "${uri}"
  fi
}

download_checkpoint "${CHECKPOINT_URI}" "${CHECKPOINT_DIR}"

CHECKPOINT_FILE=$(find "${CHECKPOINT_DIR}" -name "*.pt" -type f | head -1)
if [[ -z "${CHECKPOINT_FILE}" ]]; then
  echo "Error: No .pt checkpoint file found in ${CHECKPOINT_DIR}" >&2
  exit 1
fi
echo "Found checkpoint: ${CHECKPOINT_FILE}"

if [[ -n "${CHECKPOINT_SHA256}" ]]; then
  echo "Verifying checkpoint SHA256..."
  actual_sha256=$(sha256sum "${CHECKPOINT_FILE}" | awk '{print $1}')
  if [[ "${actual_sha256}" != "${CHECKPOINT_SHA256}" ]]; then
    echo "Error: Checkpoint SHA256 mismatch" >&2
    echo "  Expected: ${CHECKPOINT_SHA256}" >&2
    echo "  Actual:   ${actual_sha256}" >&2
    exit 1
  fi
  echo "Checkpoint SHA256 verified: ${actual_sha256}"
fi

EXPORT_DIR="${CHECKPOINT_DIR}/exported"
mkdir -p "${EXPORT_DIR}"

echo "=============================================="
echo "Exporting policy to: ${EXPORT_DIR}"
echo "=============================================="
run_python "${SCRIPT_DIR}/export_policy.py" \
    --checkpoint "${CHECKPOINT_FILE}" \
    --output-dir "${EXPORT_DIR}"

VIDEO_DIR="${EXPORT_DIR}/videos"
METRICS_DIR="${EXPORT_DIR}/metrics"
mkdir -p "${VIDEO_DIR}" "${METRICS_DIR}"

ONNX_SUCCESS=0
JIT_SUCCESS=0

run_onnx_inference() {
  echo "=============================================="
  echo "Running ONNX inference"
  echo "=============================================="

  local onnx_model="${EXPORT_DIR}/policy.onnx"
  if [[ ! -f "${onnx_model}" ]]; then
    echo "Warning: ONNX model not found at ${onnx_model}"
    return 1
  fi

  if run_isaaclab -m inference.scripts.play_policy \
      --task "${TASK}" \
      --model "${onnx_model}" \
      --format onnx \
      --num_envs "${NUM_ENVS}" \
      --max-steps "${MAX_STEPS}" \
      --video_length "${VIDEO_LENGTH}" \
      --output-metrics "${METRICS_DIR}/onnx_metrics.json" \
      ${HEADLESS} --video; then
    echo "ONNX inference completed successfully"
    return 0
  else
    echo "Warning: ONNX inference failed"
    return 1
  fi
}

run_jit_inference() {
  echo "=============================================="
  echo "Running JIT inference"
  echo "=============================================="

  local jit_model="${EXPORT_DIR}/policy.pt"
  if [[ ! -f "${jit_model}" ]]; then
    echo "Warning: JIT model not found at ${jit_model}"
    return 1
  fi

  if run_isaaclab -m inference.scripts.play_policy \
      --task "${TASK}" \
      --model "${jit_model}" \
      --format jit \
      --num_envs "${NUM_ENVS}" \
      --max-steps "${MAX_STEPS}" \
      --video_length "${VIDEO_LENGTH}" \
      --output-metrics "${METRICS_DIR}/jit_metrics.json" \
      ${HEADLESS} --video; then
    echo "JIT inference completed successfully"
    return 0
  else
    echo "Warning: JIT inference failed"
    return 1
  fi
}

case "${INFERENCE_FORMAT}" in
  onnx)
    run_onnx_inference && ONNX_SUCCESS=1
    ;;
  jit)
    run_jit_inference && JIT_SUCCESS=1
    ;;
  both)
    run_onnx_inference && ONNX_SUCCESS=1
    run_jit_inference && JIT_SUCCESS=1
    ;;
  *)
    echo "Error: Unknown inference format: ${INFERENCE_FORMAT}" >&2
    echo "Valid options: onnx, jit, both" >&2
    exit 1
    ;;
esac

echo "=============================================="
echo "Uploading artifacts"
echo "=============================================="
export EXPORT_DIR
export METRICS_DIR
export BLOB_STORAGE_ACCOUNT
export BLOB_CONTAINER
export CHECKPOINT_URI
export ONNX_SUCCESS
export JIT_SUCCESS
export TASK
export NUM_ENVS
export MAX_STEPS
export VIDEO_LENGTH
export INFERENCE_FORMAT

run_python -m inference.scripts.upload_artifacts

echo "=============================================="
echo "Inference workflow complete"
echo "=============================================="
echo "  ONNX: $( [[ ${ONNX_SUCCESS} -eq 1 ]] && echo 'SUCCESS' || echo 'SKIPPED/FAILED' )"
echo "  JIT:  $( [[ ${JIT_SUCCESS} -eq 1 ]] && echo 'SUCCESS' || echo 'SKIPPED/FAILED' )"
echo "=============================================="
