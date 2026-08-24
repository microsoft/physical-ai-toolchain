#!/bin/bash
set -euo pipefail

echo "=== LeRobot Training Workflow ==="
echo "Dataset: ${DATASET_REPO_ID}"
echo "Policy Type: ${POLICY_TYPE}"
echo "Job Name: ${JOB_NAME}"
echo "Output Dir: ${OUTPUT_DIR}"
echo "Logging: Azure ML MLflow"
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
# (MAX_ARG_STRLEN) and the archive (which ships the lerobot uv.lock
# for `uv export`) exceeds that, failing the container execve E2BIG.
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

# Decide blob-vs-HuggingFace via the canonical Python helper so
# whitespace, pretty-printed JSON, and [""] / [null] payloads agree
# with download_dataset.prepare_dataset() instead of routing through
# the blob branch and crashing.
if python -c 'from training.il.scripts.lerobot._env import has_blob_urls; raise SystemExit(0 if has_blob_urls() else 1)'; then
  BLOB_DATASOURCE=1
  echo "Data Source: Azure Blob URLs"
else
  BLOB_DATASOURCE=0
  echo "Data Source: HuggingFace Hub"
fi

# Install runtime dependencies from a build-time export of the lock
LEROBOT_PROJECT="${PAYLOAD_ROOT}/training/il/lerobot"
if [[ ! -f "${LEROBOT_PROJECT}/uv.lock" ]]; then
  echo "ERROR: LeRobot lockfile not found at ${LEROBOT_PROJECT}/uv.lock" >&2
  exit 1
fi
uv export --frozen --no-hashes --no-emit-project --project "${LEROBOT_PROJECT}" \
  | uv pip install --no-cache-dir --no-deps -r -

# Build training command arguments
TRAIN_ARGS=(
  "--policy.push_to_hub=false"
  "--wandb.enable=false"
  "--dataset.video_backend=pyav"
)

# Download dataset from Azure Blob Storage when configured
if [[ "${BLOB_DATASOURCE}" == "1" ]]; then
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
