#!/usr/bin/env bash
# AzureML entrypoint for LeRobot training jobs submitted by submit-azureml-lerobot-training.sh.
# Uploaded as part of the code asset; cwd inside the container is the contents of training/.
set -euo pipefail

echo "=== LeRobot AzureML Training ==="

# wandb is a transitive dependency of lerobot==0.4.4 (hard pin in upstream
# pyproject.toml). Setting WANDB_MODE=disabled prevents the client from
# initializing or making network calls; logging goes to MLflow / Azure ML only.
export WANDB_MODE=disabled
export WANDB_DISABLED=true

# Restore `training/` prefix so absolute references (training/il/...) and python -m
# `training.il.scripts...` resolve when cwd is the contents of training/.
if [[ ! -e training ]]; then ln -s . training; fi

# Install runtime dependencies from pre-compiled requirements
apt-get update -qq && apt-get install -y -qq ffmpeg git build-essential >/dev/null 2>&1
pip install --quiet uv

LEROBOT_REQUIREMENTS="training/il/lerobot/requirements.txt"
if [[ ! -f "${LEROBOT_REQUIREMENTS}" ]]; then
  echo "ERROR: LeRobot requirements not found at ${LEROBOT_REQUIREMENTS}" >&2
  exit 1
fi
uv pip install --system --requirement "${LEROBOT_REQUIREMENTS}"

# Build args forwarded to the MLflow training wrapper. Only flags whose values
# are not derivable from environment variables go here. The wrapper at
# training.il.scripts.lerobot.train invokes lerobot-train, parses metrics from
# stdout, streams them to MLflow, and uploads new checkpoint subdirectories
# under ${OUTPUT_DIR}/checkpoints/ to the MLflow artifact store every 60s so
# training can survive preemption / crash without losing intermediate work.
#
# `--policy.push_to_hub=false` because we register checkpoints to Azure ML, not
# HuggingFace Hub; without it lerobot-train requires `policy.repo_id`.
# `--wandb.enable=false` because logging goes through MLflow / Azure ML; we do
# not use Weights & Biases.
train_args=(
  --policy.push_to_hub=false
  --wandb.enable=false
)

# Resolve data source: Azure Blob Storage when STORAGE_ACCOUNT is set, otherwise HuggingFace Hub
if [[ -n "${STORAGE_ACCOUNT:-}" ]]; then
  echo "Downloading dataset from Azure Blob Storage (${STORAGE_ACCOUNT}/${STORAGE_CONTAINER}/${BLOB_PREFIX})..."
  python3 -m training.il.scripts.lerobot.download_dataset
  FULL_DATASET_PATH="${DATASET_ROOT}/${DATASET_REPO_ID}"
  echo "Dataset materialized at: ${FULL_DATASET_PATH}"
  # use_imagenet_stats=true so lerobot normalizes images with ImageNet
  # (3,1,1) per-channel mean/std instead of trying to use the v3.0 dataset's
  # image stats, whose shape does not match lerobot 0.4.x's normalize_processor.
  train_args+=(
    --dataset.root="${FULL_DATASET_PATH}"
    --dataset.use_imagenet_stats=true
    --dataset.video_backend=pyav
  )
elif [[ -n "${HF_TOKEN:-}" ]]; then
  python3 -c "from huggingface_hub import login; login(token='${HF_TOKEN}', add_to_git_credential=False)"
fi

echo "Running: python -m training.il.scripts.lerobot.train ${train_args[*]}"
python3 -m training.il.scripts.lerobot.train "${train_args[@]}"

echo "=== Training Complete ==="
# The wrapper invokes register_final_checkpoint() automatically when
# REGISTER_CHECKPOINT is set and the run succeeds; nothing to do here.
