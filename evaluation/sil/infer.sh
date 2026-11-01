#!/bin/bash
# Evaluation entry point for the OSMO Isaac Lab workflow: gates the checkpoint on its success rate, then
# exports it and replays the ONNX and/or JIT policy with video (--inference-format).
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

# python_cmd (array), python_exec, and PYTHONPATH come from the Isaac runtime prologue,
# sourced here so run_python/run_isaaclab can reference them. The dependency install runs
# later via setup_isaac_runtime.sh, deferred until after argument parsing.
ISAAC_PYTHONPATH_ROOT="${SRC_DIR}"
# shellcheck source=../../training/rl/scripts/isaac_python_prologue.sh
source "${SRC_DIR}/training/rl/scripts/isaac_python_prologue.sh"

# run_python uses raw Python for pip/dependency operations
run_python() {
  "${python_exec}" "$@"
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
FRAMEWORK="${FRAMEWORK:-skrl}"
EVAL_EPISODES="${EVAL_EPISODES:-100}"
SUCCESS_THRESHOLD="${SUCCESS_THRESHOLD:--1}"
OUTPUT_DIR="${OUTPUT_DIR:-}"
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
  --checkpoint-uri    Checkpoint: runs:/ or models:/ URI, Azure Blob or http(s) URL, or an
                      absolute path in the container, such as /policy in a runnable carrier
  --checkpoint-sha256  Expected SHA256 hash for checkpoint verification (required for plain http(s) URLs)
  --framework         Training framework of the checkpoint: skrl or rsl_rl (default: skrl)
  --eval-episodes     Episodes the success gate evaluates (default: 100)
  --success-threshold Success rate the gate requires (default: -1, the evaluator's default)
  --output-dir        Directory that receives the videos and metrics, such as the OSMO task output
  --headless          Run in headless mode
  -h, --help          Show this help message

Environment Variables:
  TASK, NUM_ENVS, MAX_STEPS, VIDEO_LENGTH, INFERENCE_FORMAT, CHECKPOINT_URI, CHECKPOINT_SHA256
  FRAMEWORK, EVAL_EPISODES, SUCCESS_THRESHOLD, OUTPUT_DIR
  TRACKING_BACKEND (azureml, mlflow, or none) and MLFLOW_TRACKING_URI select where runs:/ and models:/
  checkpoints download from and where results are logged
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
    --framework)
      FRAMEWORK="$2"
      shift 2
      ;;
    --eval-episodes|--eval_episodes)
      EVAL_EPISODES="$2"
      shift 2
      ;;
    --success-threshold|--success_threshold)
      SUCCESS_THRESHOLD="$2"
      shift 2
      ;;
    --output-dir|--output_dir)
      OUTPUT_DIR="$2"
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

cleanup() {
  rm -rf "${CHECKPOINT_DIR:-}"
}
trap cleanup EXIT

# Install the locked RL dependencies and configure the Isaac Sim runtime (uv, PYTHONPATH,
# python_cmd/python_exec) via the shared helper that the training entrypoint also sources.
ISAAC_PROJECT_DIR="${SRC_DIR}/training/rl"
ISAAC_PYTHONPATH_ROOT="${SRC_DIR}"
# shellcheck source=../../training/rl/scripts/setup_isaac_runtime.sh
source "${SRC_DIR}/training/rl/scripts/setup_isaac_runtime.sh"

CHECKPOINT_DIR=$(mktemp -d)
echo "=============================================="
echo "Downloading checkpoint from: ${CHECKPOINT_URI}"
echo "=============================================="

BLOB_STORAGE_ACCOUNT=""
BLOB_CONTAINER=""
download_checkpoint() {
  local uri="$1"
  local dst_dir="$2"

  if [[ "${uri}" == models:/* || "${uri}" == runs:/* ]]; then
    echo "Detected tracking URI; downloading through the ${TRACKING_BACKEND:-azureml} tracking backend"
    # The URI travels in the environment so it never becomes Python source.
    CHECKPOINT_DOWNLOAD_URI="${uri}" CHECKPOINT_DOWNLOAD_DIR="${dst_dir}" run_python << 'TRACKED_DOWNLOAD'
import os
from pathlib import Path

from training.utils.tracking import create_tracking_backend

uri = os.environ["CHECKPOINT_DOWNLOAD_URI"]
backend = create_tracking_backend(experiment_name=f"inference-{os.environ.get('TASK', 'unknown')}")
local_path = backend.download_artifacts(uri, Path(os.environ["CHECKPOINT_DOWNLOAD_DIR"]))
print(f"Downloaded {uri} to: {local_path}")
TRACKED_DOWNLOAD
  elif [[ "${uri}" == /* ]]; then
    # A path inside the task, such as /policy in a runnable carrier or a mounted dataset.
    if [[ ! -e "${uri}" ]]; then
      echo "Error: checkpoint path ${uri} does not exist in this container" >&2
      exit 1
    fi
    echo "Using the checkpoint at ${uri}"
    cp -RL "${uri}" "${dst_dir}/"
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
    if [[ -z "${CHECKPOINT_SHA256}" ]]; then
      echo "Error: --checkpoint-sha256 is required for plain http(s) checkpoint URLs" >&2
      echo "  An unauthenticated download has no trust anchor; pin the expected SHA-256 to verify integrity." >&2
      echo "  (MLflow runs:/ models:/ and *.blob.core.windows.net URIs are authenticated and may omit it.)" >&2
      exit 1
    fi
    local filename
    filename=$(basename "${uri}")
    curl -fsSL -o "${dst_dir}/${filename}" "${uri}"
  fi
}

download_checkpoint "${CHECKPOINT_URI}" "${CHECKPOINT_DIR}"

# Prefer the best checkpoint, then the highest-numbered one (agent_<step>.pt, model_<iteration>.pt).
CHECKPOINT_FILE=$(find "${CHECKPOINT_DIR}" -name "best_agent.pt" -type f | head -1)
if [[ -z "${CHECKPOINT_FILE}" ]]; then
  CHECKPOINT_FILE=$(find "${CHECKPOINT_DIR}" -name "*.pt" -type f | sort -V | tail -1)
fi
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
VIDEO_DIR="${EXPORT_DIR}/videos"
METRICS_DIR="${EXPORT_DIR}/metrics"
mkdir -p "${VIDEO_DIR}" "${METRICS_DIR}"

echo "=============================================="
echo "Gating ${FRAMEWORK} checkpoint on ${EVAL_EPISODES} episodes"
echo "=============================================="
# The evaluator exits 0 only when the success rate meets the threshold, and writes its verdict either way.
# Replay and uploads still run on a failed gate, so the videos are there to inspect.
GATE_PASSED=0
if run_isaaclab -m evaluation.sil.policy_evaluation \
    --model-path "${CHECKPOINT_FILE}" \
    --task "${TASK}" \
    --framework "${FRAMEWORK}" \
    --eval-episodes "${EVAL_EPISODES}" \
    --num-envs "${NUM_ENVS}" \
    --success-threshold "${SUCCESS_THRESHOLD}" \
    --output-metrics "${METRICS_DIR}/eval_metrics.json" \
    ${HEADLESS}; then
  GATE_PASSED=1
fi

echo "=============================================="
echo "Exporting policy to: ${EXPORT_DIR}"
echo "=============================================="
# export_policy imports torch, so it runs under the Isaac Sim interpreter that ships the
# container's torch/CUDA stack (the locked torch is intentionally not installed).
run_isaaclab "${SRC_DIR}/training/packaging/scripts/export_policy.py" \
    --checkpoint "${CHECKPOINT_FILE}" \
    --output-dir "${EXPORT_DIR}"

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

  if run_isaaclab "${SCRIPT_DIR}/play_policy.py" \
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

  if run_isaaclab "${SCRIPT_DIR}/play_policy.py" \
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

run_python "${SRC_DIR}/evaluation/metrics/upload_artifacts.py"

if [[ -n "${OUTPUT_DIR}" ]]; then
  mkdir -p "${OUTPUT_DIR}"
  cp -R "${VIDEO_DIR}" "${METRICS_DIR}" "${OUTPUT_DIR}/"
  echo "Copied videos and metrics to ${OUTPUT_DIR}"
fi

echo "=============================================="
echo "Inference workflow complete"
echo "=============================================="
echo "  Gate: $( [[ ${GATE_PASSED} -eq 1 ]] && echo 'PASSED' || echo 'FAILED' )"
echo "  ONNX: $( [[ ${ONNX_SUCCESS} -eq 1 ]] && echo 'SUCCESS' || echo 'SKIPPED/FAILED' )"
echo "  JIT:  $( [[ ${JIT_SUCCESS} -eq 1 ]] && echo 'SUCCESS' || echo 'SKIPPED/FAILED' )"
echo "=============================================="

if [[ ${GATE_PASSED} -ne 1 ]]; then
  echo "Error: the policy did not pass the success gate; see ${METRICS_DIR}/eval_metrics.json" >&2
  exit 1
fi

# Fail the job when the requested inference format did not run. ONNX is best-effort under
# "both" (JIT is the primary format), so only JIT gates "both"; an explicit onnx/jit request
# must produce a successful run of that format rather than exiting green with nothing run.
case "${INFERENCE_FORMAT}" in
  onnx)
    if [[ ${ONNX_SUCCESS} -ne 1 ]]; then
      echo "Error: ONNX inference did not complete successfully" >&2
      exit 1
    fi
    ;;
  jit | both)
    if [[ ${JIT_SUCCESS} -ne 1 ]]; then
      echo "Error: JIT inference did not complete successfully" >&2
      exit 1
    fi
    ;;
esac
