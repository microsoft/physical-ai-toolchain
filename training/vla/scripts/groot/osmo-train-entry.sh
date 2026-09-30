#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck disable=SC2154  # exit_code is assigned within the trap body below
trap 'exit_code=$?; echo "ERROR: command failed (line ${LINENO}): ${BASH_COMMAND} (exit ${exit_code})" >&2' ERR
VLA_ENTRY_START_EPOCH="$(date +%s)"
export VLA_ENTRY_START_EPOCH

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

latest_checkpoint() {
  # Echo the checkpoint-<N> dir under $1 with the highest numeric N (empty if none).
  ls -1d "$1"/checkpoint-* 2>/dev/null \
    | awk -F'checkpoint-' 'NF>1 {print $NF, $0}' \
    | sort -n \
    | tail -1 \
    | cut -d' ' -f2- || true
}

if [ -d "${DATASET_PATH}" ] && [ -n "$(ls -A "${DATASET_PATH}" 2>/dev/null)" ]; then
  echo "--- using existing local dataset at ${DATASET_PATH} ---"
  ls -lh "${DATASET_PATH}/" | head
elif [ -n "${BLOB_URL:-}" ]; then
  echo "--- downloading dataset from Azure Blob ---"
  mkdir -p "${DATASET_PATH}"
  export DEBIAN_FRONTEND=noninteractive
  retry_cmd 3 apt-get update -qq
  retry_cmd 3 apt-get install -y --no-install-recommends curl >/dev/null
  pip install --quiet azure-storage-blob==12.27.1 azure-identity==1.25.3
  python3 /tmp/download_blob.py
  echo "--- dataset download complete ---"
  ls -lh "${DATASET_PATH}/" | head
else
  echo "ERROR: no local dataset at ${DATASET_PATH} and BLOB_URL is not set" >&2
  exit 1
fi

if [ -n "${RUN_ID_OVERRIDE:-}" ]; then
  RUN_ID="${RUN_ID_OVERRIDE}"
  echo "RUN_ID_OVERRIDE set: reusing existing output dir ${RUN_ID}"
else
  RUN_ID="${WF_ID:-run-$(date -u +%Y%m%d-%H%M%S)}"
fi
OUTPUT_DIR="${OUTPUT_ROOT}/${RUN_ID}"
mkdir -p "${OUTPUT_DIR}"

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
echo "   num_gpus:    ${NUM_GPUS:-1}"
echo "========================================================"
BASE_MODEL_SOURCE="${BASE_MODEL}"
export BASE_MODEL_SOURCE

if [ -n "${MODEL_CACHE_ROOT:-}" ]; then
  MODEL_CACHE_MANIFEST="${MODEL_CACHE_ROOT}/model-cache-manifest.json"
  [ -f "${MODEL_CACHE_MANIFEST}" ] || {
    echo "ERROR: model cache manifest not found: ${MODEL_CACHE_MANIFEST}" >&2
    exit 1
  }
  python3 - "${MODEL_CACHE_MANIFEST}" "${BASE_MODEL_REVISION}" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
expected = {
    "nvidia/GR00T-N1.7-3B": sys.argv[2],
    "nvidia/Cosmos-Reason2-2B": "9ce19a195e423419c349abfc86fd07178b230561",
}
actual = {model["repository"]: model["revision"] for model in manifest["models"]}
if actual != expected:
    raise RuntimeError(f"model cache revisions do not match: {actual}")
PY
  CACHED_BASE_MODEL="${MODEL_CACHE_ROOT}/groot-n17"
  CACHED_BACKBONE_MODEL="${MODEL_CACHE_ROOT}/cosmos-reason2-2b"
  [ -f "${CACHED_BASE_MODEL}/config.json" ] || {
    echo "ERROR: cached GR00T config is missing" >&2
    exit 1
  }
  [ -f "${CACHED_BACKBONE_MODEL}/config.json" ] || {
    echo "ERROR: cached Cosmos config is missing" >&2
    exit 1
  }
  BASE_MODEL_LOCAL="/tmp/base_model"
  LOCAL_BACKBONE_MODEL="/tmp/nvidia/Cosmos-Reason2-2B"
  rm -rf "${BASE_MODEL_LOCAL}"
  rm -rf "${LOCAL_BACKBONE_MODEL}"
  mkdir -p "${BASE_MODEL_LOCAL}"
  mkdir -p "$(dirname "${LOCAL_BACKBONE_MODEL}")"
  ln -s "${CACHED_BACKBONE_MODEL}" "${LOCAL_BACKBONE_MODEL}"
  find "${CACHED_BASE_MODEL}" -mindepth 1 -maxdepth 1 ! -name config.json \
    -exec ln -s '{}' "${BASE_MODEL_LOCAL}/" \;
  python3 - "${CACHED_BASE_MODEL}/config.json" "${BASE_MODEL_LOCAL}/config.json" "${LOCAL_BACKBONE_MODEL}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
config["model_name"] = sys.argv[3]
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    json.dump(config, stream, indent=2)
    stream.write("\n")
PY
  BASE_MODEL="${BASE_MODEL_LOCAL}"
  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1
  echo "[base-model] using immutable AML model cache"
fi

NUM_GPUS="${NUM_GPUS:-1}"
if ! printf '%s' "${NUM_GPUS}" | grep -qE '^[1-9][0-9]*$'; then
  echo "ERROR: NUM_GPUS must be a positive integer (got '${NUM_GPUS}')" >&2
  exit 1
fi

nvidia-smi
df -h /dev/shm /outputs || true
echo "--- dataset ---"; ls -1 "${DATASET_PATH}" | head
echo "--- dataset/meta ---"; ls -1 "${DATASET_PATH}/meta" | head

export DEBIAN_FRONTEND=noninteractive
if ! printf '%s' "${ISAAC_GROOT_REF}" | grep -qzE '^[0-9a-fA-F]{40}$'; then
  echo "ERROR: isaac_groot_ref '${ISAAC_GROOT_REF}' must be an immutable 40-hex commit SHA" >&2
  exit 1
fi

GROOT_PREBUILT_ROOT="${GROOT_PREBUILT_ROOT:-/opt/Isaac-GR00T}"
GROOT_IMAGE_METADATA="/opt/groot-n17-image.json"
GROOT_PREBUILT=false
if [ -f "${GROOT_IMAGE_METADATA}" ] && [ -d "${GROOT_PREBUILT_ROOT}/.git" ]; then
  cd "${GROOT_PREBUILT_ROOT}"
  python3 - "${GROOT_IMAGE_METADATA}" "${ISAAC_GROOT_REF}" <<'PY'
import importlib.metadata
import json
import sys

metadata = json.load(open(sys.argv[1], encoding="utf-8"))
if metadata["isaac_groot_ref"] != sys.argv[2]:
    raise RuntimeError(
        f"prebuilt Isaac-GR00T ref {metadata['isaac_groot_ref']} "
        f"does not match requested ref {sys.argv[2]}"
    )
actual = {
    name: importlib.metadata.version(name)
    for name in metadata["runtime_versions"]
}
if actual != metadata["runtime_versions"]:
    raise RuntimeError(
        f"prebuilt runtime versions changed: expected "
        f"{metadata['runtime_versions']}, found {actual}"
    )
PY
  [ "$(git rev-parse HEAD)" = "${ISAAC_GROOT_REF}" ] || {
    echo "ERROR: prebuilt Isaac-GR00T checkout does not match ${ISAAC_GROOT_REF}" >&2
    exit 1
  }
  export PATH="${GROOT_PREBUILT_ROOT}/.venv/bin:${PATH}"
  export VIRTUAL_ENV="${GROOT_PREBUILT_ROOT}/.venv"
  GROOT_PREBUILT=true
  echo "[setup] using digest-pinned prebuilt GR00T dependency image"
else
  retry_cmd 3 apt-get update -qq
  retry_cmd 3 apt-get install -y --no-install-recommends \
    git git-lfs build-essential cmake ffmpeg python3.10-dev \
    libgl1-mesa-glx libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libvulkan-dev ca-certificates wget curl >/dev/null

  cd /workspace || cd /tmp
  if [ ! -d Isaac-GR00T ]; then
    echo "--- cloning Isaac-GR00T ---"
    retry_cmd 3 git clone https://github.com/NVIDIA/Isaac-GR00T.git
  fi
  cd Isaac-GR00T
  git lfs install --system || true

  echo "--- checking out ref: ${ISAAC_GROOT_REF} ---"
  if ! git checkout "${ISAAC_GROOT_REF}"; then
    echo "WARN: initial checkout failed; fetching ref then retrying" >&2
    git fetch origin "${ISAAC_GROOT_REF}" --depth 1 || true
    git checkout "${ISAAC_GROOT_REF}"
  fi
fi
RESOLVED_GROOT_REF="$(git rev-parse HEAD)"
export GROOT_PREBUILT RESOLVED_GROOT_REF

if [ -f "gr00t/experiment/experiment.py" ]; then
  if [ "${RESUME:-false}" = "true" ]; then
    TRAINER_RESUME_LITERAL="True"
  else
    TRAINER_RESUME_LITERAL="False"
  fi
  python3 - "gr00t/experiment/experiment.py" "${TRAINER_RESUME_LITERAL}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
resume_literal = sys.argv[2]
source = path.read_text(encoding="utf-8")
needle = "trainer.train(resume_from_checkpoint=True)"
count = source.count(needle)
if count != 2:
    raise RuntimeError(f"expected two upstream resume call sites, found {count}")
path.write_text(
    source.replace(needle, f"trainer.train(resume_from_checkpoint={resume_literal})"),
    encoding="utf-8",
)
print(f"[setup] patched N1.7 trainer resume={resume_literal}")
PY
fi

if [ "${RESUME:-false}" = "true" ]; then
  python3 - "gr00t/experiment/trainer.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = """        # During resume, don't skip the data
        self.args.ignore_data_skip = True
        curr_global_step = self.state.global_step
        print(f"Current global step: {curr_global_step}")
        if curr_global_step > 0:
            new_seed = self.train_dataset.seed + curr_global_step
            self.train_dataset.reset_seed(new_seed)
            print(
                f"Resetting seed to {new_seed}. Please note that this will make the experiment non-reproducible."
            )
"""
replacement = """        # Preserve checkpoint RNG state and let Trainer skip consumed batches.
        self.args.ignore_data_skip = False
        curr_global_step = self.state.global_step
        print(f"Current global step: {curr_global_step}")
"""
count = source.count(needle)
if count != 1:
    raise RuntimeError(f"expected one upstream non-deterministic resume block, found {count}")
path.write_text(source.replace(needle, replacement), encoding="utf-8")
print("[setup] patched N1.7 Trainer for deterministic data resume")
PY
fi

if [ -n "${MODEL_CACHE_ROOT:-}" ]; then
  python3 - "gr00t/model/gr00t_n1d7/setup.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "model_name=self.model_config.model_name,"
replacement = "model_name=self.model.config.model_name,"
count = source.count(needle)
if count != 2:
    raise RuntimeError(f"expected two upstream processor model-name call sites, found {count}")
path.write_text(source.replace(needle, replacement), encoding="utf-8")
print("[setup] patched N1.7 processor to use the cached backbone path")
PY
fi

ACCELERATE_VER="1.14.0"
NUMPY_VER="1.26.4"
OPENCV_VER="4.8.0.74"

if [ -f "gr00t/experiment/launch_finetune.py" ]; then
  if [ "${GROOT_PREBUILT}" = "true" ]; then
    echo "--- GR00T N1.7 dependencies already present in image ---"
  else
    echo "--- installing GR00T N1.7 from its immutable uv lock ---"
    python3 -m pip install --quiet uv==0.8.14
    UV_PREVIEW=1 UV_HTTP_TIMEOUT=300 UV_CONCURRENT_DOWNLOADS=4 \
      uv sync --frozen --no-install-project --no-dev --no-cache
    uv pip install --python "$(pwd)/.venv/bin/python" -e . --no-deps
    PATH="$(pwd)/.venv/bin:${PATH}"
    VIRTUAL_ENV="$(pwd)/.venv"
    export PATH VIRTUAL_ENV
  fi
else
pip install --upgrade setuptools wheel
pip install gpustat==1.1.1 wandb==0.19.0 packaging==25.0 ninja==1.13.0

# CUDA stack (torch/torchvision/torchaudio/flash-attn/numpy) is hand-pinned and
# intentionally outside Dependabot/uv-lock/OSV: versions are chosen at runtime
# per GPU (cu126 vs cu128 index), flash-attn is source-built against the chosen
# torch, and gr00t's pins are rewritten to match. Bump these together, never
# piecemeal, or the ABI breaks.
GPU_PRODUCT="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
echo "Detected GPU: ${GPU_PRODUCT}"
if echo "${GPU_PRODUCT}" | grep -qiE '5090|5080|5070|B100|B200'; then
  IS_BLACKWELL=1
else
  IS_BLACKWELL=0
fi

if [ "${IS_BLACKWELL}" = "1" ]; then
  # Blackwell needs CUDA 12.8 wheels, only on the pytorch index.
  TORCH_INDEX_ARGS=(--index-url https://download.pytorch.org/whl/cu128)
  TORCH_VER="2.7.0"
  TV_VER="0.22.0"
  TA_VER="2.7.0"
  FLASH_ATTN_VER="2.7.4.post1"
else
  # Default PyPI ships CUDA 12.6 wheels for this stack.
  TORCH_INDEX_ARGS=()
  TORCH_VER="2.7.1"
  TV_VER="0.22.1"
  TA_VER="2.7.1"
  FLASH_ATTN_VER="2.7.4.post1"
fi

# Keep flash-attn builds bounded when a wheel is unavailable.
export ACCELERATE_VER NUMPY_VER OPENCV_VER TORCH_VER TV_VER TA_VER FLASH_ATTN_VER BASE_MODEL_SOURCE
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

python -c "import torch, torchvision, flash_attn; print('torch=', torch.__version__, 'tv=', torchvision.__version__, 'cuda=', torch.version.cuda, 'flash_attn=', flash_attn.__version__)"

pip install "accelerate==${ACCELERATE_VER}"
pip install torchcodec==0.4.0 || true
fi

if [ -n "${DATA_CONFIG_B64:-}" ]; then
  DATA_CFG_PY="$(pwd)/gr00t/experiment/data_config.py"
  echo "${DATA_CONFIG_B64}" | base64 -d >> "${DATA_CFG_PY}"
  echo "  patched ${DATA_CFG_PY} with custom data config (${DATA_CONFIG})"
fi

if [ -n "${MODALITY_CONFIG_PATH:-}" ]; then
  if [ ! -f "${MODALITY_CONFIG_PATH}" ]; then
    echo "ERROR: MODALITY_CONFIG_PATH does not exist: ${MODALITY_CONFIG_PATH}" >&2
    exit 1
  fi
  cp "${MODALITY_CONFIG_PATH}" /tmp/modality_config.py
  echo "  copied modality config from ${MODALITY_CONFIG_PATH}"
elif [ -n "${MODALITY_CONFIG_B64:-}" ]; then
  echo "${MODALITY_CONFIG_B64}" | base64 -d > /tmp/modality_config.py
  echo "  wrote modality config to /tmp/modality_config.py"
fi

python -c "import torch; p=torch.cuda.get_device_properties(0); print(f'[preflight] GPU: {p.name}  total={p.total_memory/1024**3:.2f} GiB  sm={p.major}.{p.minor}')"
python -c "import sys, torch; expected=int(sys.argv[1]); actual=torch.cuda.device_count(); print(f'[preflight] visible GPUs: {actual} (expected: {expected})'); assert actual == expected, f'expected {expected} visible GPUs, found {actual}'" "${NUM_GPUS}"

VLA_SETUP_SECONDS="$(( $(date +%s) - VLA_ENTRY_START_EPOCH ))"
export VLA_SETUP_SECONDS
printf 'VLA_PHASE_TIMING={"entry_to_training_ready_seconds":%s,"prebuilt_dependencies":%s}\n' \
  "${VLA_SETUP_SECONDS}" "${GROOT_PREBUILT}"

if [ "${PREFLIGHT_ONLY:-false}" = "true" ]; then
  python - "${DATASET_PATH}" /tmp/modality_config.py <<'PY'
import importlib.util
import pathlib
import sys

dataset = pathlib.Path(sys.argv[1])
modality_config = pathlib.Path(sys.argv[2])
required = (
    dataset / "meta" / "info.json",
    dataset / "meta" / "episodes.jsonl",
    dataset / "meta" / "tasks.jsonl",
    dataset / "meta" / "modality.json",
)
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise FileNotFoundError(f"dataset is missing required metadata: {missing}")
if not any((dataset / "data").glob("**/*.parquet")):
    raise FileNotFoundError(f"dataset has no Parquet episodes: {dataset / 'data'}")
if not any((dataset / "videos").glob("**/*.mp4")):
    raise FileNotFoundError(f"dataset has no MP4 videos: {dataset / 'videos'}")

spec = importlib.util.spec_from_file_location("submitted_modality_config", modality_config)
if spec is None or spec.loader is None:
    raise ImportError(f"cannot load modality config: {modality_config}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("[preflight] dataset metadata, GR00T import, and modality registration: PASS")
PY
  cat > "${OUTPUT_DIR}/preflight-result.json" <<EOF
{
  "result": "PASS",
  "num_gpus": ${NUM_GPUS},
  "dataset_path": "${DATASET_PATH}",
  "isaac_groot_ref": "${RESOLVED_GROOT_REF}"
}
EOF
  echo "TRAIN_RESULT=PREFLIGHT_PASS"
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
  python -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(repo_id=sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3]); print('[base-model] pinned', sys.argv[1], sys.argv[2], '->', sys.argv[3])" "${BASE_MODEL}" "${BASE_MODEL_REVISION}" "${BASE_MODEL_LOCAL}"
  BASE_MODEL="${BASE_MODEL_LOCAL}"
elif [ -z "${BASE_MODEL_REVISION:-}" ] && [ ! -d "${BASE_MODEL}" ]; then
  echo "ERROR: BASE_MODEL ${BASE_MODEL} is a remote repo but base_model_revision is empty; refusing to train against a mutable HEAD" >&2
  exit 1
fi

python - "${OUTPUT_DIR}/runtime-provenance.json" <<'PY'
import importlib.metadata
import json
import os
import pathlib
import sys

packages = ("accelerate", "flash-attn", "numpy", "opencv-python", "torch", "torchaudio", "torchvision")
expected_env = {
    "accelerate": "ACCELERATE_VER",
    "flash-attn": "FLASH_ATTN_VER",
    "numpy": "NUMPY_VER",
    "opencv-python": "OPENCV_VER",
    "torch": "TORCH_VER",
    "torchaudio": "TA_VER",
    "torchvision": "TV_VER",
}
runtime_versions = {}
for name in packages:
    try:
        runtime_versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        pass

payload = {
    "base_model": os.environ["BASE_MODEL_SOURCE"],
    "base_model_revision": os.environ.get("BASE_MODEL_REVISION", ""),
    "expected_runtime_versions": {
        package: os.environ[variable]
        for package, variable in expected_env.items()
        if os.environ.get(variable)
    },
    "isaac_groot_ref": os.environ["RESOLVED_GROOT_REF"],
    "prebuilt_dependencies": os.environ.get("GROOT_PREBUILT", "false") == "true",
    "setup_seconds": int(os.environ["VLA_SETUP_SECONDS"]),
    "launcher": (
        f"torchrun --standalone --nproc_per_node={os.environ.get('NUM_GPUS', '1')}"
        if pathlib.Path("gr00t/experiment/launch_finetune.py").is_file()
        else "python"
    ),
    "resume_from_checkpoint": os.environ.get("RESUME", "false") == "true",
    "runtime_versions": runtime_versions,
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
print("VLA_RUNTIME_PROVENANCE=" + json.dumps(payload, sort_keys=True))
PY

echo "--- starting training ---"
if [ -f "scripts/gr00t_finetune.py" ]; then
  echo "  N1.5/N1.6 branch: using scripts/gr00t_finetune.py"
  python scripts/gr00t_finetune.py \
    --dataset-path "${DATASET_PATH}" \
    --output-dir "${OUTPUT_DIR}" \
    --data-config "${DATA_CONFIG}" \
    --batch-size "${BATCH_SIZE}" \
    --max-steps "${MAX_STEPS}" \
    --num-gpus "${NUM_GPUS}" \
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
    --global_batch_size "${BATCH_SIZE}"
    --dataloader_num_workers "${DATALOADER_WORKERS}"
    --num_gpus "${NUM_GPUS}"
    --tune_diffusion_model
    --tune_projector
    --save_total_limit 5
    --warmup_ratio 0.05
    --weight_decay 1e-5
    --learning_rate 1e-4
  )
  if [ -f "/tmp/modality_config.py" ]; then
    FINETUNE_ARGS+=(--modality_config_path /tmp/modality_config.py)
  fi
  if [ -z "${CUDA_VISIBLE_DEVICES:-}" ]; then
    CUDA_VISIBLE_DEVICES="$(seq -s, 0 $((NUM_GPUS - 1)))"
    export CUDA_VISIBLE_DEVICES
  fi
  torchrun --standalone --nproc_per_node="${NUM_GPUS}" \
    gr00t/experiment/launch_finetune.py "${FINETUNE_ARGS[@]}"
else
  echo "ERROR: no finetune script found in Isaac-GR00T" >&2; exit 1
fi

if [ "${RESUME:-false}" = "true" ] && [ -L "${LATEST_CKPT}" ]; then
  rm "${LATEST_CKPT}"
  echo "[resume] removed mounted checkpoint link before output upload"
fi

echo "--- training done ---"
du -sh "${OUTPUT_DIR}"
ls -lh "${OUTPUT_DIR}" | head

if [ "${AZURE_UPLOAD:-false}" = "true" ]; then
  if [ -z "${AZUREML_WORKSPACE_NAME:-}" ] || [ -z "${AZURE_SUBSCRIPTION_ID:-}" ]; then
    echo "AZURE_UPLOAD=true but AZUREML_WORKSPACE_NAME or AZURE_SUBSCRIPTION_ID not set. Skipping." >&2
  else
    echo "--- mirroring run to Azure ML ---"
    pip install --quiet \
      'mlflow==2.22.5' \
      'azureml-mlflow==1.62.0.post3' \
      'azure-identity==1.25.3' \
      'azure-ai-ml==1.34.0'
    RUN_ID="${RUN_ID}" OUTPUT_DIR="${OUTPUT_DIR}" \
    TRAINING_FRAMEWORK=groot AML_SOURCE=osmo-train \
      BASE_MODEL="${BASE_MODEL_SOURCE}" \
      BASE_MODEL_REVISION="${BASE_MODEL_REVISION:-}" \
      python /tmp/aml_mirror.py
  fi
fi

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

  pip install --quiet azure-identity==1.25.3
  ACR_HOST="${ACR_REGISTRY}.azurecr.io"
  az login --identity --allow-no-subscriptions 2>/dev/null && \
    az acr login --name "${ACR_REGISTRY}" 2>/dev/null || {
    echo "  az acr login unavailable; authenticating oras via workload identity token"
    ACR_REFRESH=$(python3 -c "import requests; from azure.identity import DefaultAzureCredential; aad=DefaultAzureCredential().get_token('https://management.azure.com/.default').token; r=requests.post('https://${ACR_HOST}/oauth2/exchange', data={'grant_type':'access_token','service':'${ACR_HOST}','access_token':aad}); r.raise_for_status(); print(r.json()['refresh_token'])")
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
