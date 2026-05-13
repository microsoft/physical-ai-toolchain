#!/usr/bin/env bash
# AzureML entrypoint for LeRobot training jobs submitted by submit-azureml-lerobot-training.sh.
# Uploaded as part of the code asset; cwd inside the container is the contents of training/.
set -euo pipefail

echo "=== LeRobot AzureML Training ==="

# Disable PEP 668 externally-managed-environment restriction in PyTorch 2.4.1+ containers
rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED 2>/dev/null || true

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

# lerobot >= 0.5 requires Python >= 3.12, but the published PyTorch images
# (pytorch/pytorch:*-cudaXX.X-cudnn9-runtime) ship Python 3.11. Use uv to
# fetch a 3.12 toolchain and install everything into a dedicated venv;
# subsequent `python3` and `lerobot-train` invocations resolve through the
# venv's bin directory once it is on PATH.
LEROBOT_VENV="/opt/lerobot-venv"
uv python install 3.12
uv venv --python 3.12 "${LEROBOT_VENV}"
# shellcheck disable=SC1091
source "${LEROBOT_VENV}/bin/activate"
uv pip install --requirement "${LEROBOT_REQUIREMENTS}"

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

# Warm-start from a previously registered policy model: load weights only;
# optimizer, scheduler, and step counter all start fresh. Setting --policy.path
# makes lerobot-train reconstruct the policy from the loaded config.json, so
# do NOT also pass --policy.type (train.py suppresses its own injection when
# --policy.path is in the CLI).
#
# AzureML exposes downloaded inputs via env vars named AZURE_ML_INPUT_<name>;
# the ${{inputs.X}} command-line template ref is NOT substituted when
# mechanism=Download (only for ro_mount). The env var name is derived from the
# input key declared by the submission script (inputs.init_from_policy_model)
# and must stay in sync with it.
init_from_policy_model_path="${AZURE_ML_INPUT_init_from_policy_model:-}"
if [[ -n "${init_from_policy_model_path}" ]]; then
  echo "[INIT-FROM-POLICY-MODEL] Source URI: ${INIT_FROM_POLICY_MODEL_SOURCE:-<unset>}"
  echo "[INIT-FROM-POLICY-MODEL] Mount path: ${init_from_policy_model_path}"
  ls -la "${init_from_policy_model_path}" || echo "[INIT-FROM-POLICY-MODEL] WARNING: mount path not listable"
  train_args+=(--policy.path="${init_from_policy_model_path}")
else
  echo "[INIT-FROM-POLICY-MODEL] Not set; training from random initialization."
fi

echo "[ENTRY] Final lerobot-train args:"
printf '  %s\n' "${train_args[@]}"

# Resolve data source: Azure Blob Storage URLs or HuggingFace Hub
if [[ -n "${BLOB_URLS:-}" ]] && [[ "${BLOB_URLS}" != "{}" ]]; then
  echo "Downloading datasets from Azure Blob Storage..."
  python3 -m training.il.scripts.lerobot.download_dataset
  FULL_DATASET_PATH="${DATASET_ROOT}/${DATASET_REPO_ID}"
  echo "Dataset materialized at: ${FULL_DATASET_PATH}"
  # use_imagenet_stats=true so lerobot normalizes images with ImageNet
  # (3,1,1) per-channel mean/std instead of trying to use the v3.0 dataset's
  # image stats, whose shape does not match lerobot 0.4.x's normalize_processor.
  # tolerance_s=0.04 (~1 frame at 30fps) accommodates real-world recording
  # jitter; the lerobot default 1e-4s is unrealistically tight and rejects
  # most non-synthetic videos. The flag is top-level (--tolerance_s), not
  # under --dataset.
  train_args+=(
    --dataset.root="${FULL_DATASET_PATH}"
    --dataset.use_imagenet_stats=true
    --dataset.video_backend=pyav
    --tolerance_s=0.04
  )
elif [[ -n "${HF_TOKEN:-}" ]]; then
  python3 -c "from huggingface_hub import login; login(token='${HF_TOKEN}', add_to_git_credential=False)"
fi

echo "Running: python -m training.il.scripts.lerobot.train ${train_args[*]}"
python3 -m training.il.scripts.lerobot.train "${train_args[@]}"

echo "=== Training Complete ==="
# The wrapper invokes register_final_checkpoint() automatically when
# REGISTER_CHECKPOINT is set and the run succeeds; nothing to do here.
