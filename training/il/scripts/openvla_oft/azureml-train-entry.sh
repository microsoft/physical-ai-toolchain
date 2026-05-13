#!/usr/bin/env bash
# AzureML entrypoint for OpenVLA-OFT fine-tuning jobs.
#
# Cwd in the container is the contents of training/; the code asset uploaded by
# submit-azureml-openvla-oft-training.sh restores the `training/` prefix via
# symlink so `python -m training.il.scripts.openvla_oft.*` resolves.
#
# Stages:
#   1. Install runtime deps (system + Python 3.10 venv via uv)
#   2. Clone moojink/openvla-oft and moojink/transformers-openvla-oft
#   3. pip install OFT + custom transformers fork + experiment requirements + flash-attn
#   4. Build RLDS dataset from blob-mounted LeRobot v3 source
#   5. Patch OFT configs/transforms/mixtures/constants for our dataset
#   6. Run torchrun vla-scripts/finetune.py and upload checkpoints
#
# Environment variables (set by submit-azureml-openvla-oft-training.sh):
#   DATASET_REPO_ID                Logical name (folder under DATASET_ROOT)
#   DATASET_ROOT                   Local mount path with LeRobot v3 dataset
#   TRAINING_CHECKPOINT_OUTPUT     AzureML upload target for checkpoints
#   OPENVLA_OFT_REF                Git ref (sha/tag/branch) for openvla-oft
#   TRANSFORMERS_FORK_REF          Git ref for moojink/transformers-openvla-oft
#   VLA_PATH                       Base VLA checkpoint (default: openvla/openvla-7b)
#   DATASET_NAME                   RLDS dataset name (TFDS builder name)
#   ACTION_DIM, PROPRIO_DIM        Robot dimensionality (12 for Schaeffler)
#   NUM_ACTIONS_CHUNK              Action chunk length (25 for 30 Hz ~0.83s)
#   IMAGE_KEY_PRIMARY              LeRobot feature for primary camera
#   IMAGE_KEY_LEFT_WRIST           LeRobot feature for left wrist camera
#   IMAGE_KEY_RIGHT_WRIST          LeRobot feature for right wrist camera
#   USE_FILM                       True/False
#   USE_PROPRIO                    True/False
#   USE_L1_REGRESSION              True/False
#   NUM_IMAGES_IN_INPUT            int
#   BATCH_SIZE                     int (per device)
#   LEARNING_RATE                  float
#   NUM_STEPS_BEFORE_DECAY         int
#   MAX_STEPS                      int
#   SAVE_FREQ                      int
#   LORA_RANK                      int
#   IMAGE_AUG                      True/False
#   NUM_GPUS                       int
#   RUN_ID_NOTE                    string appended to OFT's run_id

set -euo pipefail

echo "=== OpenVLA-OFT AzureML Training ==="

# Defaults derived from ALOHA OFT+ recipe; overridable via env.
OPENVLA_OFT_REF="${OPENVLA_OFT_REF:-main}"
TRANSFORMERS_FORK_REF="${TRANSFORMERS_FORK_REF:-main}"
VLA_PATH="${VLA_PATH:-openvla/openvla-7b}"
DATASET_NAME="${DATASET_NAME:-schaeffler_bimanual}"
ACTION_DIM="${ACTION_DIM:-12}"
PROPRIO_DIM="${PROPRIO_DIM:-12}"
NUM_ACTIONS_CHUNK="${NUM_ACTIONS_CHUNK:-25}"
USE_FILM="${USE_FILM:-True}"
USE_PROPRIO="${USE_PROPRIO:-True}"
USE_L1_REGRESSION="${USE_L1_REGRESSION:-True}"
NUM_IMAGES_IN_INPUT="${NUM_IMAGES_IN_INPUT:-3}"
BATCH_SIZE="${BATCH_SIZE:-4}"
LEARNING_RATE="${LEARNING_RATE:-5e-4}"
NUM_STEPS_BEFORE_DECAY="${NUM_STEPS_BEFORE_DECAY:-50000}"
MAX_STEPS="${MAX_STEPS:-100005}"
SAVE_FREQ="${SAVE_FREQ:-10000}"
LORA_RANK="${LORA_RANK:-32}"
IMAGE_AUG="${IMAGE_AUG:-True}"
NUM_GPUS="${NUM_GPUS:-1}"
RUN_ID_NOTE="${RUN_ID_NOTE:-physical_ai_toolchain}"

WORKSPACE="${WORKSPACE:-/workspace}"
OFT_DIR="${WORKSPACE}/openvla-oft"
TRANSFORMERS_DIR="${WORKSPACE}/transformers-openvla-oft"
RLDS_DIR="${WORKSPACE}/rlds"
RUN_ROOT_DIR="${TRAINING_CHECKPOINT_OUTPUT:-${WORKSPACE}/outputs/openvla-oft}"

mkdir -p "${WORKSPACE}" "${RLDS_DIR}" "${RUN_ROOT_DIR}"

# Restore training/ prefix
if [[ ! -e training ]]; then ln -s . training; fi

# ---- 1. System + Python 3.10 toolchain ----
apt-get update -qq && apt-get install -y -qq ffmpeg git build-essential >/dev/null 2>&1
rm -f /usr/lib/python3.*/EXTERNALLY-MANAGED 2>/dev/null || true
pip install --quiet uv

OFT_VENV="/opt/oft-venv"
uv python install 3.10
uv venv --python 3.10 "${OFT_VENV}"
# shellcheck disable=SC1091
source "${OFT_VENV}/bin/activate"

# ---- 2. Clone OFT + transformers fork ----
echo "[clone] openvla-oft@${OPENVLA_OFT_REF}"
git clone --depth=1 --branch "${OPENVLA_OFT_REF}" https://github.com/moojink/openvla-oft.git "${OFT_DIR}" \
  || git clone https://github.com/moojink/openvla-oft.git "${OFT_DIR}"
echo "[clone] transformers-openvla-oft@${TRANSFORMERS_FORK_REF}"
git clone --depth=1 --branch "${TRANSFORMERS_FORK_REF}" https://github.com/moojink/transformers-openvla-oft.git "${TRANSFORMERS_DIR}" \
  || git clone https://github.com/moojink/transformers-openvla-oft.git "${TRANSFORMERS_DIR}"

# ---- 3. Install OFT + transformers fork + extras ----
echo "[pip] base packages"
uv pip install -e "${TRANSFORMERS_DIR}"
uv pip install -e "${OFT_DIR}"
uv pip install -r "${OFT_DIR}/experiments/robot/libero/libero_requirements.txt" || true
# Flash-attention prebuilt wheels keyed to torch+cuda; let it pick the right one.
uv pip install "flash-attn==2.5.5" --no-build-isolation || \
  echo "[pip] WARNING: flash-attn install failed; finetune.py will fall back to eager attention"

# decord for video decoding inside the LeRobot->RLDS converter
uv pip install decord tensorflow tensorflow_datasets

# ---- 4. Resolve dataset source ----
#   Priority: pre-mounted AzureML data asset (DATASET_MOUNT) > BLOB_URLS download > raw
#   DATASET_ROOT pre-populated by caller.
if [[ -n "${DATASET_MOUNT:-}" ]] && [[ -d "${DATASET_MOUNT}" ]]; then
  echo "[dataset] using AzureML mounted data asset at ${DATASET_MOUNT}"
  DATASET_SOURCE="${DATASET_MOUNT}"
elif [[ -n "${BLOB_URLS:-}" ]] && [[ "${BLOB_URLS}" != "[]" ]] && [[ "${BLOB_URLS}" != "{}" ]]; then
  echo "[dataset] downloading from blob URLs via training.il.scripts.lerobot.download_dataset"
  python -m training.il.scripts.lerobot.download_dataset
  DATASET_SOURCE="${DATASET_ROOT}/${DATASET_REPO_ID}"
else
  DATASET_SOURCE="${DATASET_ROOT}/${DATASET_REPO_ID}"
  echo "[dataset] using pre-populated ${DATASET_SOURCE}"
fi

if [[ ! -d "${DATASET_SOURCE}" ]]; then
  echo "ERROR: dataset not found at ${DATASET_SOURCE}" >&2
  exit 1
fi

MANIFEST_PATH="${WORKSPACE}/training_manifest.json"

echo "[filter] LeRobot dataset @ ${DATASET_SOURCE}"
python -m training.il.scripts.openvla_oft.filter_dataset \
  --dataset "${DATASET_SOURCE}" \
  --image-keys "${IMAGE_KEY_PRIMARY}" "${IMAGE_KEY_LEFT_WRIST}" "${IMAGE_KEY_RIGHT_WRIST}" \
  --output "${MANIFEST_PATH}"

echo "[rlds] building TFDS dataset under ${RLDS_DIR}/${DATASET_NAME}"
python -m training.il.scripts.openvla_oft.lerobot_to_rlds \
  --manifest "${MANIFEST_PATH}" \
  --primary-camera "${IMAGE_KEY_PRIMARY}" \
  --left-wrist "${IMAGE_KEY_LEFT_WRIST}" \
  --right-wrist "${IMAGE_KEY_RIGHT_WRIST}" \
  --name "${DATASET_NAME}" \
  --output-dir "${RLDS_DIR}"

# ---- 5. Patch OFT configs/transforms/mixtures/constants ----
python -m training.il.scripts.openvla_oft.dataset_registration \
  --oft-root "${OFT_DIR}" \
  --dataset-name "${DATASET_NAME}" \
  --action-dim "${ACTION_DIM}" \
  --proprio-dim "${PROPRIO_DIM}" \
  --num-actions-chunk "${NUM_ACTIONS_CHUNK}"

# ---- 6. Train ----
cd "${OFT_DIR}"

train_cmd=(
  torchrun --standalone --nnodes 1 --nproc-per-node "${NUM_GPUS}"
  vla-scripts/finetune.py
  --vla_path "${VLA_PATH}"
  --data_root_dir "${RLDS_DIR}"
  --dataset_name "${DATASET_NAME}"
  --run_root_dir "${RUN_ROOT_DIR}"
  --use_l1_regression "${USE_L1_REGRESSION}"
  --use_diffusion False
  --use_film "${USE_FILM}"
  --num_images_in_input "${NUM_IMAGES_IN_INPUT}"
  --use_proprio "${USE_PROPRIO}"
  --batch_size "${BATCH_SIZE}"
  --learning_rate "${LEARNING_RATE}"
  --num_steps_before_decay "${NUM_STEPS_BEFORE_DECAY}"
  --max_steps "${MAX_STEPS}"
  --save_freq "${SAVE_FREQ}"
  --save_latest_checkpoint_only False
  --image_aug "${IMAGE_AUG}"
  --lora_rank "${LORA_RANK}"
  --run_id_note "${RUN_ID_NOTE}"
)

echo "[train] ${train_cmd[*]}"
exec "${train_cmd[@]}"
