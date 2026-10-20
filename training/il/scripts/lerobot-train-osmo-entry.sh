#!/bin/bash
set -euo pipefail

echo "=== LeRobot Training Workflow ==="
echo "Dataset: ${DATASET_REPO_ID}"
echo "Policy Type: ${POLICY_TYPE}"
echo "Job Name: ${JOB_NAME}"
echo "Output Dir: ${OUTPUT_DIR}"
echo "Tracking: ${TRACKING_BACKEND:-azureml}"
echo "Val Split: ${VAL_SPLIT:-0.1}"
echo "System Metrics: ${SYSTEM_METRICS:-true}"
echo "Mixed Precision: ${MIXED_PRECISION:-no}"

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

# Install UV package manager. --break-system-packages bypasses
# PEP 668 (externally-managed-environment), which is safe in this
# ephemeral container and portable across Debian, conda, and
# PyTorch base images regardless of where the marker file lives.
echo "Installing UV package manager..."
pip install --quiet --break-system-packages uv==0.7.12

echo "Creating Python 3.12 environment..."
uv python install 3.12
uv venv --python 3.12 /opt/lerobot-venv
# shellcheck disable=SC1091
source /opt/lerobot-venv/bin/activate

# Unpack training scripts delivered via the OSMO url: input. The code
# archive arrives as a downloaded object under the input mount rather
# than an env var: a single env string is capped at 128 KiB
# (MAX_ARG_STRLEN) and the archive (which ships the LeRobot project and
# lock for frozen synchronization) exceeds that, failing execve with E2BIG.
PAYLOAD_ROOT="${PAYLOAD_ROOT:-/workspace/lerobot_payload}"
mkdir -p "${PAYLOAD_ROOT}"
ARCHIVE_PATH="$(find "${OSMO_INPUT_0}" -maxdepth 2 -name '*.zip' | head -n1)"
if [[ -z "${ARCHIVE_PATH}" || ! -s "${ARCHIVE_PATH}" ]]; then
  echo "ERROR: no code archive found under the input mount; training payload is required." >&2
  ls -laR "${OSMO_INPUT_0}" || true
  exit 1
fi
unzip -oq "${ARCHIVE_PATH}" -d "${PAYLOAD_ROOT}"
export PYTHONPATH="${PAYLOAD_ROOT}:${PYTHONPATH:-}"
echo "Training scripts unpacked to ${PAYLOAD_ROOT} from ${ARCHIVE_PATH}"

# Choose the data source with the canonical Python helper so whitespace,
# pretty-printed JSON, and [""] / [null] payloads agree with
# download_dataset.prepare_dataset() instead of routing through the blob
# branch and crashing.
DATASET_SOURCE="$(python -c 'from training.il.scripts.lerobot._env import dataset_source; print(dataset_source())')"
case "${DATASET_SOURCE}" in
  local)
    echo "Data Source: local dataset at ${DATASET_DIR}"
    if [[ ! -f "${DATASET_DIR}/meta/info.json" ]]; then
      echo "ERROR: no LeRobot dataset at ${DATASET_DIR} (expected meta/info.json)" >&2
      exit 1
    fi
    ;;
  blob) echo "Data Source: Azure Blob URLs" ;;
  *) echo "Data Source: HuggingFace Hub" ;;
esac

# Install runtime dependencies directly from the frozen lock of the selected
# LeRobot project (training/il/lerobot by default; VLA policies use their own).
LEROBOT_PROJECT_DIR="${LEROBOT_PROJECT:-training/il/lerobot}"
case "${LEROBOT_PROJECT_DIR}" in
  /*|../*|*/../*|*/..)
    echo "ERROR: LEROBOT_PROJECT must be a repo-relative path without parent traversal: ${LEROBOT_PROJECT_DIR}" >&2
    exit 1
    ;;
esac
LEROBOT_PROJECT_PATH="${PAYLOAD_ROOT}/${LEROBOT_PROJECT_DIR}"
if [[ ! -f "${LEROBOT_PROJECT_PATH}/uv.lock" ]]; then
  echo "ERROR: LeRobot lockfile not found at ${LEROBOT_PROJECT_PATH}/uv.lock" >&2
  exit 1
fi
uv sync --active --frozen --no-config --no-install-project --project "${LEROBOT_PROJECT_PATH}"

# Build training command arguments
TRAIN_ARGS=(
  "--policy.push_to_hub=false"
  "--wandb.enable=false"
  "--dataset.video_backend=pyav"
)

# A local dataset is complete on disk, so LeRobot loads it from its root without the Hub.
if [[ "${DATASET_SOURCE}" == "local" ]]; then
  TRAIN_ARGS+=("--dataset.root=${DATASET_DIR}")
fi

# Download dataset from Azure Blob Storage when configured
if [[ "${DATASET_SOURCE}" == "blob" ]]; then
  echo "Downloading dataset from Azure Blob Storage..."
  python -m training.il.scripts.lerobot.download_dataset

  FULL_DATASET_PATH="${DATASET_ROOT}/${DATASET_REPO_ID}"
  echo "Dataset downloaded to: ${FULL_DATASET_PATH}"
  TRAIN_ARGS+=(
    "--dataset.root=${FULL_DATASET_PATH}"
    "--dataset.use_imagenet_stats=false"
  )
fi

# Run training via Python orchestrator
echo "Starting LeRobot training..."
python -m training.il.scripts.lerobot.train "${TRAIN_ARGS[@]}"

echo "=== Training Complete ==="
ls -la "${OUTPUT_DIR}/" 2>/dev/null || true
