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
# --break-system-packages bypasses PEP 668 (externally-managed-environment)
# enforced by Debian-packaged Python in PyTorch 2.4.1+ containers. Safe here
# because the container is ephemeral and isolated from any host system Python.
pip install --quiet --break-system-packages uv

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
#
# `--no-deps` is required: requirements.txt is a fully-resolved lockfile
# emitted by `uv pip compile`, and pyproject.toml carries `override-dependencies`
# entries (e.g., azure-storage-blob==12.29.0 above azureml-mlflow's <=12.27.1
# cap) that are honored at compile time only. Re-resolving at install time
# would fail with the same conflicts the overrides were added to bypass.
LEROBOT_VENV="${LEROBOT_VENV:-/opt/lerobot-venv}"
uv python install 3.12
uv venv --python 3.12 "${LEROBOT_VENV}"
# shellcheck disable=SC1091
source "${LEROBOT_VENV}/bin/activate"
uv pip install --no-cache-dir --no-deps --requirement "${LEROBOT_REQUIREMENTS}"

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

# GPU topology diagnostics: log visible devices before train.py runs the
# torch.cuda detection. On AzureML-on-Kubernetes the InstanceType chosen at
# submission determines the container's `nvidia.com/gpu` allocation; on
# AmlCompute it is the cluster VM SKU's GPU count. train.py auto-wraps with
# `accelerate launch` when the visible count is > 1.
echo "[ENTRY] CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}"
echo "[ENTRY] MIXED_PRECISION=${MIXED_PRECISION:-no}"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L || true
fi

# Resolve data sources: AzureML Data Assets (ro_mount), Azure Blob Storage, HuggingFace Hub.
# Multiple assets and blobs can be combined; when there are multiple total sources they
# are merged via lerobot-edit-dataset.

# Collect mounted data asset paths from AZURE_ML_INPUT_dataset_asset_N env vars.
asset_paths=()
asset_count="${DATASET_ASSET_COUNT:-0}"
if [[ ! "${asset_count}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: DATASET_ASSET_COUNT must be a non-negative integer, got '${asset_count}'" >&2
  exit 1
fi
# Upper bound guards against template-substitution bugs that could otherwise
# loop millions of times before failing. Raise if a legitimate use case appears.
DATASET_ASSET_COUNT_MAX=64
if [[ ${asset_count} -gt ${DATASET_ASSET_COUNT_MAX} ]]; then
  echo "ERROR: DATASET_ASSET_COUNT=${asset_count} exceeds maximum of ${DATASET_ASSET_COUNT_MAX}" >&2
  exit 1
fi

for (( i=0; i<asset_count; i++ )); do
  varname="AZURE_ML_INPUT_dataset_asset_${i}"
  path="${!varname:-}"
  if [[ -n "${path}" ]]; then
    echo "[DATASET-ASSET ${i}] Mounted at: ${path}"
    find "${path}" -maxdepth 1 -mindepth 1 -print 2>/dev/null | sed -n "1,10p" || \
      echo "[DATASET-ASSET ${i}] WARNING: mount path not listable"
    asset_paths+=("${path}")
  fi
done
if [[ ${asset_count} -gt 0 && ${#asset_paths[@]} -ne ${asset_count} ]]; then
  echo "ERROR: Expected ${asset_count} AzureML data asset mount(s), but found ${#asset_paths[@]}." >&2
  echo "ERROR: Mount env var status (check AzureML input names and ro_mount configuration):" >&2
  for (( i=0; i<asset_count; i++ )); do
    varname="AZURE_ML_INPUT_dataset_asset_${i}"
    echo "  ${varname}=${!varname:-<UNSET>}" >&2
  done
  exit 1
fi

# Collect blob-downloaded datasets. Delegate the decision to the canonical
# Python helper so whitespace, pretty-printed JSON, and [""] / [null] payloads
# agree with download_dataset.prepare_dataset() instead of being routed into
# the blob branch only to crash on parse.
blob_paths=()
if python3 -c 'from training.il.scripts.lerobot._env import has_blob_urls; raise SystemExit(0 if has_blob_urls() else 1)'; then
  echo "Downloading datasets from Azure Blob Storage..."
  python3 -m training.il.scripts.lerobot.download_dataset
  FULL_DATASET_PATH="${DATASET_ROOT:-/workspace/data}/${DATASET_REPO_ID}"
  echo "Dataset materialized at: ${FULL_DATASET_PATH}"
  blob_paths+=("${FULL_DATASET_PATH}")
fi

# Determine final dataset path based on total source count.
all_sources=()
if [[ ${#asset_paths[@]} -gt 0 ]]; then
  all_sources+=("${asset_paths[@]}")
fi
if [[ ${#blob_paths[@]} -gt 0 ]]; then
  all_sources+=("${blob_paths[@]}")
fi
total_sources=${#all_sources[@]}

if [[ ${total_sources} -eq 0 ]]; then
  # No mounted assets or blobs — fall back to HuggingFace Hub. The wrapper at
  # train.py injects --dataset.repo_id from $DATASET_REPO_ID, so refuse the
  # zero-source path when that env var is empty to avoid an opaque crash deep
  # inside lerobot's dataset factory.
  if [[ -z "${DATASET_REPO_ID:-}" ]]; then
    echo "ERROR: no dataset_asset_*, no blob URLs, and DATASET_REPO_ID is empty." >&2
    echo "Pass --dataset-asset, --blob-url, or --dataset-repo-id to submit-azureml-lerobot-training.sh." >&2
    exit 1
  fi
  if [[ -n "${HF_TOKEN:-}" ]]; then
    python3 -c "import os; from huggingface_hub import login; login(token=os.environ['HF_TOKEN'], add_to_git_credential=False)"
  fi
  # video_backend=pyav avoids torchcodec's dynamic-link dependency on
  # libnvrtc.so (shipped as a pip wheel whose lib/ is not on LD_LIBRARY_PATH
  # in a fresh venv). Consistent with the local-data paths below.
  train_args+=(--dataset.video_backend=pyav)
elif [[ ${total_sources} -eq 1 ]]; then
  # Single source — use directly, no merge needed.
  # use_imagenet_stats=true so lerobot normalizes images with ImageNet (3,1,1)
  # per-channel mean/std instead of trying to use the v3.0 dataset's image stats,
  # whose shape does not match lerobot 0.4.x's normalize_processor.
  # video_backend=pyav is the most reliable decoder for the AzureML container.
  # tolerance_s=0.04 (~1 frame at 30fps) accommodates real-world recording jitter;
  # the lerobot default 1e-4s is unrealistically tight and rejects most non-synthetic
  # videos. The flag is top-level (--tolerance_s), not under --dataset.
  train_args+=(
    --dataset.root="${all_sources[0]}"
    --dataset.use_imagenet_stats=true
    --dataset.video_backend=pyav
    --tolerance_s=0.04
  )
else
  # Multiple sources — merge into a single dataset via lerobot-edit-dataset.
  echo "Merging ${total_sources} dataset sources..."
  MERGE_DEST="${DATASET_ROOT:-/workspace/data}/merged"
  python3 -c "
import shlex, subprocess, shutil, sys
from pathlib import Path

dest = Path(sys.argv[1])
sources = sys.argv[2:]
if dest.exists():
    shutil.rmtree(dest)

import json
cmd = [
    'lerobot-edit-dataset',
    '--new_repo_id', 'merged',
    '--operation.type', 'merge',
    '--operation.repo_ids', json.dumps(list(map(str, range(len(sources))))),
    '--operation.roots', json.dumps(sources),
    '--new_root', str(dest),
]
print(f'Running: {shlex.join(cmd)}', flush=True)
result = subprocess.run(cmd, capture_output=True, text=True)
if result.stdout:
    print(result.stdout)
if result.stderr:
    print(result.stderr, file=sys.stderr)
if result.returncode != 0:
    print(f'lerobot-edit-dataset failed with exit code {result.returncode}', file=sys.stderr)
    sys.exit(1)
if not dest.exists():
    print(f'lerobot-edit-dataset did not create {dest}', file=sys.stderr)
    sys.exit(1)
# Sanity-check merged dataset structure so a corrupt merge fails at merge time
# rather than midway through training.
info_candidates = [dest / 'meta' / 'info.json', dest / 'info.json']
info_found = next((p for p in info_candidates if p.is_file()), None)
if info_found is None:
    print(f'Merged dataset at {dest} is missing info.json in {[str(p) for p in info_candidates]}', file=sys.stderr)
    sys.exit(1)
try:
    with info_found.open(encoding='utf-8') as f:
        info = json.load(f)
except json.JSONDecodeError as exc:
    print(f'Merged dataset info.json is not valid JSON: {info_found}: {exc}', file=sys.stderr)
    sys.exit(1)
if not isinstance(info, dict):
    print(f'Merged dataset info.json must contain a JSON object: {info_found}', file=sys.stderr)
    sys.exit(1)
missing_keys = [key for key in ('total_episodes', 'total_frames', 'features') if key not in info]
if missing_keys:
    print(f'Merged dataset info.json is missing required keys {missing_keys}: {info_found}', file=sys.stderr)
    sys.exit(1)
print(f'Merged dataset at: {dest} (info: {info_found})')
" "${MERGE_DEST}" "${all_sources[@]}"

  # Same lerobot flags as the single-source path; see comment above for rationale.
  train_args+=(
    --dataset.root="${MERGE_DEST}"
    --dataset.use_imagenet_stats=true
    --dataset.video_backend=pyav
    --tolerance_s=0.04
  )
fi

echo "Running: python -m training.il.scripts.lerobot.train ${train_args[*]}"
python3 -m training.il.scripts.lerobot.train "${train_args[@]}"

echo "=== Training Complete ==="
# The wrapper invokes register_final_checkpoint() automatically when
# REGISTER_CHECKPOINT is set and the run succeeds; nothing to do here.
