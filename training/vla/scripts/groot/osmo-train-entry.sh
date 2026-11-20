#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck disable=SC2154  # exit_code is assigned within the trap body below
trap 'exit_code=$?; echo "ERROR: command failed (line ${LINENO}): ${BASH_COMMAND} (exit ${exit_code})" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOAD_BLOB_PY="${DOWNLOAD_BLOB_PY:-/tmp/download_blob.py}"
if [ ! -f "${DOWNLOAD_BLOB_PY}" ] && [ -f "${SCRIPT_DIR}/download_blob.py" ]; then
  DOWNLOAD_BLOB_PY="${SCRIPT_DIR}/download_blob.py"
fi
AML_MIRROR_PY="${AML_MIRROR_PY:-/tmp/aml_mirror.py}"
if [ ! -f "${AML_MIRROR_PY}" ] && [ -f "${SCRIPT_DIR}/../../../utils/aml_mirror.py" ]; then
  AML_MIRROR_PY="$(cd "${SCRIPT_DIR}/../../.." && pwd)/utils/aml_mirror.py"
fi

DATASET_PATH="${DATASET_PATH:-/data/dataset}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/outputs}"
DATA_CONFIG="${DATA_CONFIG:-example}"
BATCH_SIZE="${BATCH_SIZE:-4}"
MAX_STEPS="${MAX_STEPS:-500}"
SAVE_STEPS="${SAVE_STEPS:-100}"
LOGGING_STEPS="${LOGGING_STEPS:-10}"
DATALOADER_WORKERS="${DATALOADER_WORKERS:-0}"
EMBODIMENT_TAG="${EMBODIMENT_TAG:-new_embodiment}"
ISAAC_GROOT_REF="${ISAAC_GROOT_REF:-23ace64f17aa5015259b8609d371eb61a357c776}"
SKIP_RUNTIME_BOOTSTRAP="${SKIP_RUNTIME_BOOTSTRAP:-false}"
VERIFY_DATASET="${VERIFY_DATASET:-true}"
SMOKE_KERNEL_ONLY="${SMOKE_KERNEL_ONLY:-false}"
RUN_AUTH_PREFLIGHT="${RUN_AUTH_PREFLIGHT:-false}"

if [ -n "${RUN_ID_OVERRIDE:-}" ]; then
  RUN_ID="${RUN_ID_OVERRIDE}"
  echo "RUN_ID_OVERRIDE set: reusing existing output dir ${RUN_ID}"
else
  RUN_ID="${WF_ID:-run-$(date -u +%Y%m%d-%H%M%S)}"
fi
OUTPUT_DIR="${OUTPUT_ROOT}/${RUN_ID}"
mkdir -p "${OUTPUT_DIR}"
HYDRATION_RECEIPT_PATH="${HYDRATION_RECEIPT_PATH:-${OUTPUT_DIR}/hydration-receipt.json}"
export RUN_ID OUTPUT_DIR DATASET_PATH HYDRATION_RECEIPT_PATH

WORKFLOW_COMPLETED=0
HYDRATE_S=0
BOOTSTRAP_S=0
TRAIN_S=0
GPU_PRODUCT=""

active_python() {
  if command -v python >/dev/null 2>&1; then
    echo "python"
  else
    echo "python3"
  fi
}

resolve_tools_python() {
  local candidate
  local -a candidates=(python3 python3.11 python)
  if [[ -n "${AML_TOOLS_PYTHON:-}" ]]; then
    candidates=("${AML_TOOLS_PYTHON}")
  fi
  for candidate in "${candidates[@]}"; do
    if command -v "${candidate}" >/dev/null 2>&1 &&
      "${candidate}" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
      "${candidate}" -c 'import sys; print(sys.executable)'
      return
    fi
  done
  echo "ERROR: set AML_TOOLS_PYTHON to a Python 3.11+ interpreter for Azure helpers." >&2
  return 1
}

# Keep the tools venv's executable, not its symlink target, across GR00T's conda activation.
TOOLS_PYTHON="$(resolve_tools_python)"

emit_hook() {
  local phase="$1"
  shift
  if [ -f "${AML_MIRROR_PY}" ]; then
    DETECTED_GPU_PRODUCT="${GPU_PRODUCT}" "${TOOLS_PYTHON}" "${AML_MIRROR_PY}" \
      --emit-runhook --phase "${phase}" "$@" || {
        if [ "${RUNHOOK_STRICT:-false}" = "true" ]; then
          return 1
        fi
        echo "WARN: runhook '${phase}' encountered an error; continuing" >&2
      }
  fi
}

run_hook_script() {
  local script_path="$1"
  local hook_name="$2"
  if [ -n "${script_path}" ]; then
    if [ ! -f "${script_path}" ]; then
      echo "ERROR: ${hook_name} hook script not found: ${script_path}" >&2
      exit 1
    fi
    echo "--- running ${hook_name} hook: ${script_path} ---"
    RUN_MANIFEST_PATH="${OUTPUT_DIR}/run-manifest.json" bash "${script_path}"
  fi
}

on_workflow_exit() {
  local rc=$?
  if [ "${rc}" -ne 0 ] && [ "${WORKFLOW_COMPLETED}" -ne 1 ]; then
    emit_hook FAILED --exit-code "${rc}" \
      --hydrate-s "${HYDRATE_S}" \
      --bootstrap-s "${BOOTSTRAP_S}" \
      --train-s "${TRAIN_S}" || true
  fi
}
trap on_workflow_exit EXIT

retry_cmd() {
  local max_attempts="$1"
  shift
  local attempt=1
  while true; do
    if "$@"; then
      return 0
    fi
    if [ "${attempt}" -ge "${max_attempts}" ]; then
      echo "ERROR: command failed after ${attempt} attempts: $*" >&2
      return 1
    fi
    attempt=$((attempt + 1))
    echo "WARN: retry ${attempt}/${max_attempts}: $*" >&2
  done
}

ensure_azure_blob_deps() {
  if "${TOOLS_PYTHON}" -c "import azure.identity, azure.storage.blob" >/dev/null 2>&1; then
    return 0
  fi
  if [ "${SKIP_RUNTIME_BOOTSTRAP}" = "true" ]; then
    echo "ERROR: azure-identity and azure-storage-blob must be pre-installed when SKIP_RUNTIME_BOOTSTRAP=true" >&2
    exit 1
  fi
  "${TOOLS_PYTHON}" -m pip install --quiet azure-storage-blob==12.27.1 azure-identity==1.25.3
}

latest_checkpoint() {
  # Echo the checkpoint-<N> dir under $1 with the highest numeric N (empty if none).
  ls -1d "$1"/checkpoint-* 2>/dev/null \
    | awk -F'checkpoint-' 'NF>1 {print $NF, $0}' \
    | sort -n \
    | tail -1 \
    | cut -d' ' -f2- || true
}

emit_hook PREFLIGHT

if [ "${RUN_AUTH_PREFLIGHT}" = "true" ]; then
  REPO_CANDIDATE="$(cd "${SCRIPT_DIR}/../../../.." 2>/dev/null && pwd || true)"
  if [ -n "${REPO_CANDIDATE}" ] && [ -f "${REPO_CANDIDATE}/training/rl/scripts/smoke_test_azure.py" ]; then
    echo "--- running Azure connectivity and auth preflight ---"
    PYTHONPATH="${REPO_CANDIDATE}" "${TOOLS_PYTHON}" -m training.rl.scripts.smoke_test_azure \
      --forbid-static-secrets --verify-all-scopes --require-artifact-upload --probe-network
  else
    echo "WARN: RUN_AUTH_PREFLIGHT=true, but training/rl/scripts/smoke_test_azure.py is not in workspace tree; skipping" >&2
  fi
fi

run_hook_script "${PRE_HYDRATE_HOOK:-}" "pre_hydrate"
emit_hook HYDRATING
HYDRATE_START=$SECONDS

if [ -d "${DATASET_PATH}" ] && [ -n "$(ls -A "${DATASET_PATH}" 2>/dev/null)" ]; then
  echo "--- using existing local dataset at ${DATASET_PATH} ---"
  ls -lh "${DATASET_PATH}/" | head
  if [ "${VERIFY_DATASET}" = "true" ] && [ -f "${DOWNLOAD_BLOB_PY}" ]; then
    "${TOOLS_PYTHON}" "${DOWNLOAD_BLOB_PY}" --verify-only --dataset-path "${DATASET_PATH}" --receipt-out "${HYDRATION_RECEIPT_PATH}"
  fi
elif [ -n "${AZUREML_DATASET_ASSET:-}" ] || [ -n "${BLOB_URL:-}" ]; then
  echo "--- hydrating dataset onto worker (${AZUREML_DATASET_ASSET:-${BLOB_URL%%\?*}}) ---"
  mkdir -p "${DATASET_PATH}"
  ensure_azure_blob_deps
  "${TOOLS_PYTHON}" "${DOWNLOAD_BLOB_PY}" --dataset-path "${DATASET_PATH}" --receipt-out "${HYDRATION_RECEIPT_PATH}"
  echo "--- dataset hydration complete ---"
  ls -lh "${DATASET_PATH}/" | head
else
  echo "ERROR: no local dataset at ${DATASET_PATH} and neither AZUREML_DATASET_ASSET nor BLOB_URL is set" >&2
  exit 1
fi

BASE_MODEL_SOURCE="${BASE_MODEL:-}"

if [ -n "${BASE_MODEL_BLOB_URL:-}" ] && [ ! -d "${BASE_MODEL:-}" ]; then
  echo "--- hydrating base model from Azure Blob ---"
  BASE_MODEL_LOCAL="${BASE_MODEL_LOCAL:-/tmp/base_model}"
  mkdir -p "${BASE_MODEL_LOCAL}"
  ensure_azure_blob_deps
  BLOB_URL="${BASE_MODEL_BLOB_URL}" AZUREML_DATASET_ASSET="" \
    "${TOOLS_PYTHON}" "${DOWNLOAD_BLOB_PY}" --dataset-path "${BASE_MODEL_LOCAL}" --skip-schema-check --receipt-out ""
  BASE_MODEL="${BASE_MODEL_LOCAL}"
  if [ -z "${BASE_MODEL_SOURCE}" ]; then
    BASE_MODEL_SOURCE="${BASE_MODEL_BLOB_URL%%\?*}"
  fi
fi

HYDRATE_S=$((SECONDS - HYDRATE_START))

if [ "${RESUME:-false}" = "true" ]; then
  RESUME_FLAG="--resume"
  LATEST_CKPT="$(latest_checkpoint "${OUTPUT_DIR}")"
  if [ -z "${LATEST_CKPT}" ]; then
    echo "ERROR: RESUME=true but no checkpoint-* found in ${OUTPUT_DIR}" >&2
    exit 1
  fi
  echo "Resuming from checkpoint: ${LATEST_CKPT}"
else
  RESUME_FLAG="--no-resume"
fi

echo "========================================================"
echo " LeRobot / GR00T fine-tune"
echo "   node:        $(hostname)"
echo "   date (utc):  $(date -u)"
echo "   dataset:     ${DATASET_PATH}"
echo "   output:      ${OUTPUT_DIR}"
echo "   base model:  ${BASE_MODEL}"
echo "   data config: ${DATA_CONFIG}"
echo "   batch_size:  ${BATCH_SIZE}"
echo "   max_steps:   ${MAX_STEPS}"
echo "========================================================"

nvidia-smi
GPU_PRODUCT="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
echo "Detected GPU: ${GPU_PRODUCT}"
df -h /dev/shm /outputs || true
echo "--- dataset ---"; ls -1 "${DATASET_PATH}" | head
echo "--- dataset/meta ---"; ls -1 "${DATASET_PATH}/meta" | head

# Pin Isaac-GR00T to an immutable commit: git tags/branches are mutable, so a
# non-SHA ref could silently check out new upstream code between runs.
if ! printf '%s' "${ISAAC_GROOT_REF}" | grep -qzE '^[0-9a-fA-F]{40}$'; then
  echo "ERROR: isaac_groot_ref '${ISAAC_GROOT_REF}' must be an immutable 40-hex commit SHA" >&2
  exit 1
fi

BOOTSTRAP_START=$SECONDS
ACCELERATE_VER="1.14.0"
NUMPY_VER="1.26.4"
OPENCV_VER="4.8.0.74"
if echo "${GPU_PRODUCT}" | grep -qiE '5090|5080|5070|B100|B200'; then
  IS_BLACKWELL=1
  TORCH_VER="2.7.0"
  TV_VER="0.22.0"
  TA_VER="2.7.0"
  FLASH_ATTN_VER="2.7.4.post1"
else
  IS_BLACKWELL=0
  TORCH_VER="2.7.1"
  TV_VER="0.22.1"
  TA_VER="2.7.1"
  FLASH_ATTN_VER="2.7.4.post1"
fi
export ACCELERATE_VER NUMPY_VER OPENCV_VER TORCH_VER TV_VER TA_VER FLASH_ATTN_VER BASE_MODEL_SOURCE

if [ "${SKIP_RUNTIME_BOOTSTRAP}" = "true" ]; then
  echo "--- SKIP_RUNTIME_BOOTSTRAP=true: using pre-hydrated container environment ---"
  if [ -n "${GROOT_REPO_DIR:-}" ] && [ -d "${GROOT_REPO_DIR}" ]; then
    cd "${GROOT_REPO_DIR}"
  elif [ -d "/workspace/Isaac-GR00T" ]; then
    cd /workspace/Isaac-GR00T
  elif [ -d "/tmp/Isaac-GR00T" ]; then
    cd /tmp/Isaac-GR00T
  else
    echo "ERROR: SKIP_RUNTIME_BOOTSTRAP=true requires GROOT_REPO_DIR or /workspace/Isaac-GR00T" >&2
    exit 1
  fi
  if [ -d ".git" ]; then
    ACTIVE_HEAD="$(git rev-parse HEAD)"
    if [ "${ACTIVE_HEAD}" != "${ISAAC_GROOT_REF}" ]; then
      echo "ERROR: pre-hydrated Isaac-GR00T HEAD (${ACTIVE_HEAD}) != ISAAC_GROOT_REF (${ISAAC_GROOT_REF})" >&2
      exit 1
    fi
    RESOLVED_GROOT_REF="${ACTIVE_HEAD}"
  else
    RESOLVED_GROOT_REF="${ISAAC_GROOT_REF}"
  fi
  export RESOLVED_GROOT_REF
  if [ -d "/opt/conda/envs/gr00t-py310" ] && [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    source /opt/conda/etc/profile.d/conda.sh
    conda activate gr00t-py310
  fi
else
  export DEBIAN_FRONTEND=noninteractive
  retry_cmd 3 apt-get update -qq
  retry_cmd 3 apt-get install -y --no-install-recommends \
    git git-lfs build-essential cmake ffmpeg \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libvulkan-dev ca-certificates wget curl >/dev/null

  if [ -n "${GROOT_REPO_DIR:-}" ] && [ -d "${GROOT_REPO_DIR}" ]; then
    cd "${GROOT_REPO_DIR}"
  else
    cd /workspace || cd /tmp
    if [ ! -d Isaac-GR00T ]; then
      echo "--- cloning Isaac-GR00T ---"
      retry_cmd 3 git clone https://github.com/NVIDIA/Isaac-GR00T.git
    fi
    cd Isaac-GR00T
  fi
  git lfs install --system || true

  echo "--- checking out ref: ${ISAAC_GROOT_REF} ---"
  if ! git checkout "${ISAAC_GROOT_REF}"; then
    echo "WARN: initial checkout failed; fetching ref then retrying" >&2
    git fetch origin "${ISAAC_GROOT_REF}" --depth 1 || true
    git checkout "${ISAAC_GROOT_REF}"
  fi
  RESOLVED_GROOT_REF="$(git rev-parse HEAD)"
  export RESOLVED_GROOT_REF

  # GR00T N1.7+ pins python==3.10.*; the default pytorch image ships
  # python 3.11. Create a conda env when the active interpreter is
  # not 3.10 so the editable install can resolve.
  if [ -f "gr00t/experiment/launch_finetune.py" ]; then
    CURRENT_PY="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [ "${CURRENT_PY}" != "3.10" ] && command -v conda >/dev/null 2>&1; then
      echo "[setup] GR00T N1.7 requires Python 3.10 (active: ${CURRENT_PY}); creating conda env"
      conda create -y -n gr00t-py310 python=3.10
      # shellcheck disable=SC1091
      source /opt/conda/etc/profile.d/conda.sh
      conda activate gr00t-py310
      pip install --upgrade pip setuptools wheel
    fi
  fi

  pip install --upgrade setuptools wheel
  pip install gpustat==1.1.1 wandb==0.19.0 packaging==25.0 ninja==1.13.0

  # CUDA stack (torch/torchvision/torchaudio/flash-attn/numpy) is hand-pinned and
  # intentionally outside Dependabot/uv-lock/OSV: versions are chosen at runtime
  # per GPU (cu126 vs cu128 index), flash-attn is source-built against the chosen
  # torch, and gr00t's pins are rewritten to match. Bump these together, never
  # piecemeal, or the ABI breaks.
  if [ "${IS_BLACKWELL}" = "1" ]; then
    # Blackwell needs CUDA 12.8 wheels, only on the pytorch index.
    TORCH_INDEX_ARGS=(--index-url https://download.pytorch.org/whl/cu128)
  else
    # Default PyPI ships CUDA 12.6 wheels for this stack.
    TORCH_INDEX_ARGS=()
  fi

  # Keep flash-attn builds bounded when a wheel is unavailable.
  export MAX_JOBS="${MAX_JOBS:-2}"
  export NVCC_THREADS="${NVCC_THREADS:-1}"

  # gr00t pins an older torch/torchvision in its packaging extras (torch==2.5.1
  # for N1.5). Installing the editable package as-is downgrades the CUDA stack we
  # just built flash-attn against, forcing a multi-hundred-MB wheel re-download
  # and a second reinstall to undo it. Instead: install the torch stack once
  # under a constraints file, build flash-attn against it once, rewrite gr00t's
  # pins to this stack, then install the package constrained so nothing churns.
  cat > /tmp/torch-constraints.txt <<EOF
torch==${TORCH_VER}
torchvision==${TV_VER}
torchaudio==${TA_VER}
numpy==${NUMPY_VER}
EOF

  pip install --force-reinstall --timeout 600 --retries 5 "${TORCH_INDEX_ARGS[@]}" \
    -c /tmp/torch-constraints.txt \
    torch=="${TORCH_VER}" torchvision=="${TV_VER}" torchaudio=="${TA_VER}" numpy=="${NUMPY_VER}"

  pip install --force-reinstall --timeout 600 --retries 5 --prefer-binary --no-deps --no-build-isolation \
    flash_attn=="${FLASH_ATTN_VER}"

  sed -i -E \
    -e "s/\"torch==[^\"]*\"/\"torch==${TORCH_VER}\"/g" \
    -e "s/\"torchvision==[^\"]*\"/\"torchvision==${TV_VER}\"/g" \
    -e "s/\"torchaudio==[^\"]*\"/\"torchaudio==${TA_VER}\"/g" \
    pyproject.toml

  pip install -c /tmp/torch-constraints.txt -e ".[base]"
  pip uninstall -y transformer-engine || true
  pip uninstall -y opencv-python opencv-python-headless || true
  rm -rf /usr/local/lib/python3.10/dist-packages/cv2 /usr/local/lib/python3.11/dist-packages/cv2 || true
  pip install opencv-python=="${OPENCV_VER}"

  pip install "accelerate==${ACCELERATE_VER}"
  pip install torchcodec==0.4.0 || true
fi

PY_BIN="$(active_python)"
"${PY_BIN}" -c "import torch, torchvision, flash_attn; print('torch=', torch.__version__, 'tv=', torchvision.__version__, 'cuda=', torch.version.cuda, 'flash_attn=', flash_attn.__version__)"

if [ -n "${DATA_CONFIG_B64:-}" ]; then
  DATA_CFG_PY="$(pwd)/gr00t/experiment/data_config.py"
  echo "${DATA_CONFIG_B64}" | base64 -d >> "${DATA_CFG_PY}"
  echo "  patched ${DATA_CFG_PY} with custom data config (${DATA_CONFIG})"
fi

if [ -n "${MODALITY_CONFIG_B64:-}" ]; then
  echo "${MODALITY_CONFIG_B64}" | base64 -d > /tmp/modality_config.py
  echo "  wrote modality config to /tmp/modality_config.py"
fi

"${PY_BIN}" -c "import torch; p=torch.cuda.get_device_properties(0); x=torch.randn((256, 256), device='cuda', dtype=torch.bfloat16); y=(x @ x.T).float(); torch.cuda.synchronize(); assert torch.isfinite(y).all(); print(f'[preflight] GPU: {p.name}  total={p.total_memory/1024**3:.2f} GiB  sm={p.major}.{p.minor}  bf16_matmul=ok')"

BOOTSTRAP_S=$((SECONDS - BOOTSTRAP_START))

if [ "${SMOKE_KERNEL_ONLY}" = "true" ]; then
  echo "--- SMOKE_KERNEL_ONLY=true: GPU kernel preflight passed; skipping finetune ---"
  emit_hook COMPLETED --exit-code 0 --hydrate-s "${HYDRATE_S}" --bootstrap-s "${BOOTSTRAP_S}" --train-s 0
  WORKFLOW_COMPLETED=1
  exit 0
fi

# Pin the base model to an immutable commit before the (external) finetune
# script resolves it: nvcr/HF tags are mutable, so an upstream repo could
# otherwise ship new weights silently. When BASE_MODEL is a HuggingFace repo
# id (not an on-disk path) and a revision is set, snapshot it locally at that
# SHA and point BASE_MODEL at the snapshot.
if [ -n "${BASE_MODEL_REVISION:-}" ] && [ ! -d "${BASE_MODEL}" ]; then
  if ! printf '%s' "${BASE_MODEL_REVISION}" | grep -qzE '^[0-9a-fA-F]{40}$'; then
    echo "ERROR: base_model_revision '${BASE_MODEL_REVISION}' must be an immutable 40-hex commit SHA for remote BASE_MODEL '${BASE_MODEL}'" >&2
    exit 1
  fi
  echo "--- pinning base model ${BASE_MODEL} @ ${BASE_MODEL_REVISION} ---"
  BASE_MODEL_LOCAL="/tmp/base_model"
  "${PY_BIN}" -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3]); print('[base-model] pinned', sys.argv[1], sys.argv[2], '->', sys.argv[3])" "${BASE_MODEL}" "${BASE_MODEL_REVISION}" "${BASE_MODEL_LOCAL}"
  BASE_MODEL="${BASE_MODEL_LOCAL}"
elif [ -z "${BASE_MODEL_REVISION:-}" ] && [ ! -d "${BASE_MODEL}" ]; then
  echo "ERROR: BASE_MODEL ${BASE_MODEL} is a remote repo but base_model_revision is empty; refusing to train against a mutable HEAD" >&2
  exit 1
fi

"${PY_BIN}" - "${OUTPUT_DIR}/runtime-provenance.json" <<'PY'
import importlib.metadata
import json
import os
import pathlib
import sys

packages = ("accelerate", "flash-attn", "numpy", "opencv-python", "torch", "torchaudio", "torchvision")
runtime_versions = {}
for name in packages:
    try:
        runtime_versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        runtime_versions[name] = "not-installed"
payload = {
    "base_model": os.environ.get("BASE_MODEL_SOURCE") or os.environ.get("BASE_MODEL", ""),
    "base_model_revision": os.environ.get("BASE_MODEL_REVISION", ""),
    "expected_runtime_versions": {
        "accelerate": os.environ["ACCELERATE_VER"],
        "flash-attn": os.environ["FLASH_ATTN_VER"],
        "numpy": os.environ["NUMPY_VER"],
        "opencv-python": os.environ["OPENCV_VER"],
        "torch": os.environ["TORCH_VER"],
        "torchaudio": os.environ["TA_VER"],
        "torchvision": os.environ["TV_VER"],
    },
    "isaac_groot_ref": os.environ["RESOLVED_GROOT_REF"],
    "runtime_versions": runtime_versions,
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
print("VLA_RUNTIME_PROVENANCE=" + json.dumps(payload, sort_keys=True))
PY

run_hook_script "${PRE_TRAIN_HOOK:-}" "pre_train"
emit_hook RUNNING --hydrate-s "${HYDRATE_S}" --bootstrap-s "${BOOTSTRAP_S}"
TRAIN_START=$SECONDS

echo "--- starting training ---"
if [ -f "scripts/gr00t_finetune.py" ]; then
  echo "  N1.5/N1.6 branch: using scripts/gr00t_finetune.py"
  "${PY_BIN}" scripts/gr00t_finetune.py \
    --dataset-path "${DATASET_PATH}" \
    --output-dir "${OUTPUT_DIR}" \
    --data-config "${DATA_CONFIG}" \
    --batch-size "${BATCH_SIZE}" \
    --max-steps "${MAX_STEPS}" \
    --num-gpus 1 \
    --save-steps "${SAVE_STEPS}" \
    --base-model-path "${BASE_MODEL}" \
    --no-tune-llm \
    --tune-visual \
    --tune-projector \
    --tune-diffusion-model \
    ${RESUME_FLAG} \
    --dataloader-num-workers "${DATALOADER_WORKERS}" \
    --report-to tensorboard \
    --embodiment-tag "${EMBODIMENT_TAG}"
elif [ -f "gr00t/experiment/launch_finetune.py" ]; then
  echo "  N1.7+ branch: using gr00t/experiment/launch_finetune.py"
  FINETUNE_ARGS=(
    --base_model_path "${BASE_MODEL}"
    --dataset_path "${DATASET_PATH}"
    --embodiment_tag "${EMBODIMENT_TAG}"
    --output_dir "${OUTPUT_DIR}"
    --max_steps "${MAX_STEPS}"
    --save_steps "${SAVE_STEPS}"
    --logging_steps "${LOGGING_STEPS}"
    --report_to tensorboard
    --global_batch_size "${BATCH_SIZE}"
    --dataloader_num_workers "${DATALOADER_WORKERS}"
    --num_gpus 1
    --tune_diffusion_model
    --tune_projector
    --save_total_limit 5
    --warmup_ratio 0.05
    --weight_decay 1e-5
    --learning_rate 1e-4
  )
  if [ -f "/tmp/modality_config.py" ]; then
    FINETUNE_ARGS+=(--modality_config_path /tmp/modality_config.py)
  elif [ -n "${MODALITY_CONFIG_FILE:-}" ] && [ -f "${MODALITY_CONFIG_FILE}" ]; then
    FINETUNE_ARGS+=(--modality_config_path "${MODALITY_CONFIG_FILE}")
  fi
  export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
  "${PY_BIN}" gr00t/experiment/launch_finetune.py "${FINETUNE_ARGS[@]}"
else
  echo "ERROR: no finetune script found in Isaac-GR00T" >&2; exit 1
fi

TRAIN_S=$((SECONDS - TRAIN_START))
echo "--- training done (${TRAIN_S}s) ---"
du -sh "${OUTPUT_DIR}"
ls -lh "${OUTPUT_DIR}" | head
run_hook_script "${POST_TRAIN_HOOK:-}" "post_train"

MIRROR_COMPLETED=0
if [ "${AZURE_UPLOAD:-false}" = "true" ]; then
  EFFECTIVE_RG="${AZURE_RESOURCE_GROUP:-${AZUREML_RESOURCE_GROUP:-}}"
  if [ -z "${AZUREML_WORKSPACE_NAME:-}" ] || [ -z "${AZURE_SUBSCRIPTION_ID:-}" ] || [ -z "${EFFECTIVE_RG}" ]; then
    msg="AZURE_UPLOAD=true requires AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP (or AZUREML_RESOURCE_GROUP), and AZUREML_WORKSPACE_NAME."
    if [ "${AZURE_UPLOAD_STRICT:-false}" = "true" ]; then
      echo "ERROR: ${msg}" >&2
      exit 1
    fi
    echo "${msg} Skipping." >&2
  else
    echo "--- mirroring run to Azure ML ---"
    emit_hook MIRRORING --hydrate-s "${HYDRATE_S}" --bootstrap-s "${BOOTSTRAP_S}" --train-s "${TRAIN_S}"
    if ! "${TOOLS_PYTHON}" -c "import mlflow, azureml.mlflow, azure.ai.ml, azure.identity, azure.storage.blob, tbparse" >/dev/null 2>&1; then
      if [ "${SKIP_RUNTIME_BOOTSTRAP}" = "true" ]; then
        echo "ERROR: pre-install mlflow, azureml-mlflow, azure-ai-ml, azure-identity, azure-storage-blob and tbparse in AML_TOOLS_PYTHON." >&2
        exit 1
      fi
      "${TOOLS_PYTHON}" -m pip install --quiet \
        'mlflow==2.22.5' \
        'azureml-mlflow==1.62.0.post3' \
        'azure-identity==1.25.3' \
        'azure-ai-ml==1.34.0' \
        'tbparse==0.0.9'
    fi
    if AZURE_RESOURCE_GROUP="${EFFECTIVE_RG}" RUN_ID="${RUN_ID}" OUTPUT_DIR="${OUTPUT_DIR}" \
      AZUREML_MODEL_NAME="${AZUREML_MODEL_NAME:-groot-model}" \
      BASE_MODEL="${BASE_MODEL_SOURCE:-${BASE_MODEL}}" \
      BASE_MODEL_REVISION="${BASE_MODEL_REVISION:-}" \
      DETECTED_GPU_PRODUCT="${GPU_PRODUCT}" \
      TRAINING_FRAMEWORK=groot AML_SOURCE="${AML_SOURCE:-osmo-train}" \
      "${TOOLS_PYTHON}" "${AML_MIRROR_PY}"; then
      MIRROR_COMPLETED=1
    else
      if [ "${AZURE_UPLOAD_STRICT:-false}" = "true" ]; then
        echo "ERROR: Azure ML mirror failed and AZURE_UPLOAD_STRICT=true" >&2
        exit 1
      fi
      echo "WARN: Azure ML mirror failed; local run at ${OUTPUT_DIR} is unaffected." >&2
    fi
  fi
fi

if [ "${MIRROR_COMPLETED}" -ne 1 ]; then
  emit_hook COMPLETED --exit-code 0 --hydrate-s "${HYDRATE_S}" --bootstrap-s "${BOOTSTRAP_S}" --train-s "${TRAIN_S}"
fi
WORKFLOW_COMPLETED=1

if [ -n "${ACR_REGISTRY:-}" ]; then
  echo "--- pushing model to ACR: ${ACR_REGISTRY} ---"
  ORAS_VERSION="1.2.0"
  # Canonical hash recorded in scripts/security/tool-checksums.json; bump both together.
  ORAS_SHA256="5b3f1cbb86d869eee68120b9b45b9be983f3738442f87ee5f06b00edd0bab336"
  # -f makes curl exit non-zero on an HTTP error instead of saving the error body.
  curl -fsSLO "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz"
  echo "${ORAS_SHA256}  oras_${ORAS_VERSION}_linux_amd64.tar.gz" | sha256sum -c -
  tar xzf "oras_${ORAS_VERSION}_linux_amd64.tar.gz" -C /usr/local/bin/ oras
  chmod +x /usr/local/bin/oras
  rm -f "oras_${ORAS_VERSION}_linux_amd64.tar.gz"

  ensure_azure_blob_deps
  ACR_HOST="${ACR_REGISTRY}.azurecr.io"
  az login --identity --allow-no-subscriptions 2>/dev/null && \
    az acr login --name "${ACR_REGISTRY}" 2>/dev/null || {
    echo "  az acr login unavailable; authenticating oras via workload identity token"
    ACR_REFRESH=$("${TOOLS_PYTHON}" -c "import requests; from azure.identity import DefaultAzureCredential; aad=DefaultAzureCredential().get_token('https://management.azure.com/.default').token; r=requests.post('https://${ACR_HOST}/oauth2/exchange', data={'grant_type':'access_token','service':'${ACR_HOST}','access_token':aad}); r.raise_for_status(); print(r.json()['refresh_token'])")
    printf '%s' "${ACR_REFRESH}" | oras login "${ACR_HOST}" --username 00000000-0000-0000-0000-000000000000 --password-stdin
    unset ACR_REFRESH
  }

  FINAL_CKPT="$(latest_checkpoint "${OUTPUT_DIR}")"
  if [ -z "${FINAL_CKPT}" ]; then
    echo "WARN: no checkpoint-* dirs found; skipping ACR push" >&2
  else
    STEP="$(basename "${FINAL_CKPT}" | sed 's/checkpoint-//')"
    MODEL_REPO="${ACR_MODEL_REPO:-models/groot}"
    TAG="${RUN_ID}-step${STEP}"
    REF="${ACR_HOST}/${MODEL_REPO}:${TAG}"

    echo "  checkpoint: ${FINAL_CKPT}"
    echo "  pushing to: ${REF}"
    cd "${FINAL_CKPT}"
    oras push "${REF}" \
      --annotation "org.opencontainers.image.title=${MODEL_REPO}" \
      --annotation "training.run_id=${RUN_ID}" \
      --annotation "training.step=${STEP}" \
      --annotation "training.base_model=${BASE_MODEL}" \
      ./:application/vnd.nvidia.groot.checkpoint.v1 || \
      echo "WARN: ACR push failed; local checkpoint at ${FINAL_CKPT} is unaffected." >&2
    cd -
    echo "  ACR push complete: ${REF}"
  fi
fi

echo "TRAIN_RESULT=PASS"
