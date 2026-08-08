#!/bin/bash
# OSMO entry script for LeRobot evaluation.
#
# Base64-injected into the workflow by submit-osmo-lerobot-eval.sh and decoded
# by the task bootstrap. Sets up the LeRobot runtime, unpacks the code archive
# delivered via the OSMO url: input, then invokes the shared evaluation module
# evaluation/sil/scripts/run_evaluation.py — the same entrypoint the AzureML
# path runs, so both submission paths execute one canonical implementation.
set -euo pipefail

echo "=== LeRobot Inference Workflow ==="
echo "Policy: ${POLICY_REPO_ID:-none}"
echo "Policy Type: ${POLICY_TYPE}"
echo "Dataset: ${DATASET_REPO_ID:-${DATASET_DIR:-none}}"
echo "Eval Episodes: ${EVAL_EPISODES}"
echo "Output Dir: ${OUTPUT_DIR}"
echo "AML Model: ${AML_MODEL_NAME:-none}:${AML_MODEL_VERSION:-none}"
echo "Builtin Policy: ${BUILTIN_POLICY:-false}"

# Install system dependencies
echo "Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq -o Acquire::Retries=3
APT_PACKAGES=(
  ffmpeg
  libgl1
  build-essential
  gcc
  unzip
  python3-dev
)
if apt-cache show libglib2.0-0t64 >/dev/null 2>&1; then
  APT_PACKAGES+=(libglib2.0-0t64)
else
  APT_PACKAGES+=(libglib2.0-0)
fi
apt-get install -y -qq --no-install-recommends "${APT_PACKAGES[@]}"

# Install UV package manager. --break-system-packages bypasses PEP 668
# (externally-managed-environment), which is safe in this ephemeral container
# and portable across Debian, conda, and PyTorch base images.
echo "Installing UV package manager..."
pip install --quiet --break-system-packages uv==0.7.12

# LeRobot requires Python >= 3.12; the base image ships 3.11. Create a
# dedicated 3.12 venv (uv downloads the interpreter) so `uv export` of the
# lerobot lock resolves, matching the training workflow.
echo "Creating Python 3.12 environment..."
uv python install 3.12
uv venv --python 3.12 /opt/lerobot-venv
# shellcheck disable=SC1091
source /opt/lerobot-venv/bin/activate

# Unpack runtime payload delivered via the OSMO url: input. The code archive
# arrives as a downloaded object under the input mount rather than an env var:
# a single env string is capped at 128 KiB (MAX_ARG_STRLEN) and the archive
# (which ships the lerobot uv.lock for `uv export`) exceeds that, failing the
# container execve E2BIG.
PAYLOAD_ROOT="${PAYLOAD_ROOT:-/workspace/lerobot_payload}"
mkdir -p "${PAYLOAD_ROOT}"
ARCHIVE_PATH="$(find "${OSMO_INPUT_0}" -maxdepth 2 -name '*.zip' | head -n1)"
if [[ -z "${ARCHIVE_PATH}" || ! -s "${ARCHIVE_PATH}" ]]; then
  echo "ERROR: no code archive found under the input mount; runtime payload is required." >&2
  ls -laR "${OSMO_INPUT_0}" || true
  exit 1
fi
unzip -oq "${ARCHIVE_PATH}" -d "${PAYLOAD_ROOT}"
export PYTHONPATH="${PAYLOAD_ROOT}:${PYTHONPATH:-}"
echo "Runtime payload unpacked to ${PAYLOAD_ROOT} from ${ARCHIVE_PATH}"

EVAL_SCRIPTS="${PAYLOAD_ROOT}/evaluation/sil/scripts"

# Install runtime dependencies from a build-time export of the committed lock.
# The lerobot lock already provides av, pyarrow, matplotlib, mlflow-skinny,
# azureml-mlflow, and the azure SDKs the evaluation and its helpers need.
LEROBOT_PROJECT="${PAYLOAD_ROOT}/training/il/lerobot"
if [[ ! -f "${LEROBOT_PROJECT}/uv.lock" ]]; then
  echo "ERROR: LeRobot lockfile not found at ${LEROBOT_PROJECT}/uv.lock" >&2
  exit 1
fi
uv export --frozen --no-hashes --no-emit-project --project "${LEROBOT_PROJECT}" \
  | uv pip install --no-cache-dir --no-deps -r -

# Download policy from the AzureML model registry if requested.
if [[ -n "${AML_MODEL_NAME:-}" && "${AML_MODEL_NAME}" != "none" && -n "${AML_MODEL_VERSION:-}" && "${AML_MODEL_VERSION}" != "none" ]]; then
  echo "Downloading model from AzureML registry: ${AML_MODEL_NAME}:${AML_MODEL_VERSION}..."
  python3 "${EVAL_SCRIPTS}/download_aml_model.py"

  if [[ -f /tmp/aml_model_path.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/aml_model_path.env | xargs)
    export POLICY_REPO_ID="${AML_MODEL_PATH}"
    echo "Using AzureML model at: ${POLICY_REPO_ID}"
  else
    echo "ERROR: Model download did not produce path file" >&2
    exit 1
  fi
fi

# Download dataset from Azure Blob Storage if configured.
if [[ -n "${BLOB_STORAGE_ACCOUNT:-}" && "${BLOB_STORAGE_ACCOUNT}" != "none" && -n "${BLOB_PREFIX:-}" && "${BLOB_PREFIX}" != "none" ]]; then
  echo "Downloading dataset from Azure Blob: ${BLOB_STORAGE_ACCOUNT}/${BLOB_STORAGE_CONTAINER}/${BLOB_PREFIX}..."
  python3 "${EVAL_SCRIPTS}/download_blob_dataset.py"

  if [[ -f /tmp/dataset_path.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/dataset_path.env | xargs)
    echo "Dataset ready at: ${DATASET_DIR}"
  fi
fi

# Mint a self-contained base policy from LeRobot's built-in architecture when
# requested, removing the dependency on an external policy. The eval test only
# asserts metrics exist, so an untrained policy suffices; lerobot-train writes a
# `from_pretrained`-loadable checkpoint identical in shape to a Hub policy.
# Requires the local blob dataset (DATASET_DIR), set above.
if [[ "${BUILTIN_POLICY:-false}" == "true" ]]; then
  echo "Minting built-in ${POLICY_TYPE} base policy from dataset..."
  if [[ -z "${DATASET_DIR:-}" || ! -d "${DATASET_DIR:-}" ]]; then
    echo "ERROR: --builtin-policy requires a local dataset; set --from-blob-dataset." >&2
    exit 1
  fi

  MINT_DEVICE=cpu
  if python3 -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)"; then
    MINT_DEVICE=cuda
  fi

  # A single optimizer step is the minimum that triggers a checkpoint save
  # (lerobot writes the checkpoint inside the step loop, so 0 steps saves
  # nothing). pretrained_backbone_weights=null keeps the mint fully offline
  # (random ResNet init; irrelevant for an untrained smoke policy). repo_id is
  # an arbitrary local label because --dataset.root points at the downloaded
  # dataset tree.
  BASE_POLICY_OUTPUT=/workspace/base-policy
  lerobot-train \
    --policy.type="${POLICY_TYPE}" \
    --policy.device="${MINT_DEVICE}" \
    --policy.pretrained_backbone_weights=null \
    --policy.push_to_hub=false \
    --dataset.repo_id=dataset \
    --dataset.root="${DATASET_DIR}" \
    --dataset.use_imagenet_stats=false \
    --dataset.video_backend=pyav \
    --output_dir="${BASE_POLICY_OUTPUT}" \
    --steps=1 \
    --save_freq=1 \
    --batch_size=1 \
    --num_workers=0 \
    --wandb.enable=false

  POLICY_REPO_ID="${BASE_POLICY_OUTPUT}/checkpoints/last/pretrained_model"
  export POLICY_REPO_ID
  if [[ ! -f "${POLICY_REPO_ID}/model.safetensors" ]]; then
    echo "ERROR: base policy mint did not produce ${POLICY_REPO_ID}/model.safetensors" >&2
    ls -laR "${BASE_POLICY_OUTPUT}" || true
    exit 1
  fi
  echo "Built-in base policy ready at: ${POLICY_REPO_ID}"
fi

# Authenticate with HuggingFace Hub.
if [[ -n "${HF_TOKEN:-}" ]]; then
  echo "Authenticating with HuggingFace Hub..."
  python3 -c "import os; from huggingface_hub import login; login(token=os.environ['HF_TOKEN'], add_to_git_credential=False)"
else
  echo "Warning: HF_TOKEN not set, skipping HuggingFace authentication"
fi

# Bootstrap Azure ML MLflow tracking.
if [[ "${MLFLOW_ENABLE:-false}" == "true" ]]; then
  echo "Configuring Azure ML MLflow tracking..."
  if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || -z "${AZURE_RESOURCE_GROUP:-}" || -z "${AZUREML_WORKSPACE_NAME:-}" ]]; then
    echo "ERROR: MLflow requires AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME" >&2
    exit 1
  fi

  python3 "${PAYLOAD_ROOT}/evaluation/metrics/bootstrap_mlflow.py"

  if [[ -f /tmp/mlflow_config.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/mlflow_config.env | xargs)
  fi
fi

# Run evaluation via the shared canonical module.
echo "Starting LeRobot evaluation..."
mkdir -p "${OUTPUT_DIR}"
python3 "${EVAL_SCRIPTS}/run_evaluation.py"
echo "=== Evaluation Complete ==="

# Register model to Azure ML if requested.
if [[ -n "${REGISTER_MODEL:-}" && "${REGISTER_MODEL}" != "none" ]]; then
  echo "=== Registering Model to Azure ML ==="
  if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" || -z "${AZURE_RESOURCE_GROUP:-}" || -z "${AZUREML_WORKSPACE_NAME:-}" ]]; then
    echo "Warning: Azure ML variables not set, skipping registration"
  else
    python3 "${PAYLOAD_ROOT}/workflows/azureml/scripts/register_model.py"
    echo "=== Model Registration Complete ==="
  fi
fi
