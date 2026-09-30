#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRAINING_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

required=(
  DATASET_PATH
  OUTPUT_ROOT
  AZURE_SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP
  AZUREML_WORKSPACE_NAME
)
for name in "${required[@]}"; do
  if [ -z "${!name:-}" ]; then
    echo "ERROR: required environment variable is empty: ${name}" >&2
    exit 1
  fi
done

if [ -z "${MODALITY_CONFIG_PATH:-}" ] && [ -z "${MODALITY_CONFIG_B64:-}" ]; then
  echo "ERROR: MODALITY_CONFIG_PATH or MODALITY_CONFIG_B64 is required" >&2
  exit 1
fi

if [ -n "${MODALITY_CONFIG_PATH:-}" ]; then
  case "${MODALITY_CONFIG_PATH}" in
    /*) ;;
    *) MODALITY_CONFIG_PATH="${TRAINING_ROOT}/${MODALITY_CONFIG_PATH}" ;;
  esac
  export MODALITY_CONFIG_PATH
fi

DATASET_MOUNT="${DATASET_PATH}"
DATASET_OVERLAY="/tmp/azureml-dataset"
rm -rf "${DATASET_OVERLAY}"
mkdir -p "${DATASET_OVERLAY}"
for entry in "${DATASET_MOUNT}"/*; do
  [ -e "${entry}" ] || continue
  if [ "$(basename "${entry}")" = "meta" ]; then
    cp -a "${entry}" "${DATASET_OVERLAY}/meta"
  else
    ln -s "${entry}" "${DATASET_OVERLAY}/$(basename "${entry}")"
  fi
done
[ -d "${DATASET_OVERLAY}/meta" ] || {
  echo "ERROR: mounted dataset has no meta directory" >&2
  exit 1
}
export DATASET_PATH="${DATASET_OVERLAY}"
echo "[azureml] created writable metadata overlay for the read-only dataset mount"

if [ "${RESUME:-false}" = "true" ]; then
  : "${RESUME_CHECKPOINT:?RESUME_CHECKPOINT is required when RESUME=true}"
  [ -d "${RESUME_CHECKPOINT}" ] || {
    echo "ERROR: resume checkpoint input is not a directory: ${RESUME_CHECKPOINT}" >&2
    exit 1
  }
  latest_resume_checkpoint="$(
    find "${RESUME_CHECKPOINT}" -type d -name 'checkpoint-*' |
      sort -V |
      tail -n 1
  )"
  [ -n "${latest_resume_checkpoint}" ] || {
    echo "ERROR: resume checkpoint input contains no trainer_state.json" >&2
    exit 1
  }
  [ -f "${latest_resume_checkpoint}/trainer_state.json" ] || {
    echo "ERROR: latest resume checkpoint contains no trainer_state.json" >&2
    exit 1
  }
  resume_output="${OUTPUT_ROOT}/${RUN_ID_OVERRIDE:-training}"
  mkdir -p "${resume_output}"
  ln -s "${latest_resume_checkpoint}" \
    "${resume_output}/$(basename "${latest_resume_checkpoint}")"
  echo "[azureml] linked resume checkpoint: ${latest_resume_checkpoint}"
fi

if [ -n "${HF_TOKEN_SECRET_URL:-}" ]; then
  [ -z "${HF_TOKEN:-}" ] || {
    echo "ERROR: HF_TOKEN and HF_TOKEN_SECRET_URL cannot both be set" >&2
    exit 1
  }
  HF_TOKEN="$(python3 - <<'PY'
import json
import os
import urllib.parse
import urllib.request

identity_query = {
    "api-version": "2018-02-01",
    "resource": "https://vault.azure.net",
}
if os.environ.get("AZURE_CLIENT_ID"):
    identity_query["client_id"] = os.environ["AZURE_CLIENT_ID"]
identity_url = (
    "http://169.254.169.254/metadata/identity/oauth2/token?"
    + urllib.parse.urlencode(identity_query)
)
identity_request = urllib.request.Request(identity_url, headers={"Metadata": "true"})
imds_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with imds_opener.open(identity_request, timeout=30) as response:
    access_token = json.load(response)["access_token"]

secret_url = os.environ["HF_TOKEN_SECRET_URL"]
separator = "&" if "?" in secret_url else "?"
secret_request = urllib.request.Request(
    secret_url + separator + "api-version=7.4",
    headers={"Authorization": f"Bearer {access_token}"},
)
with urllib.request.urlopen(secret_request, timeout=30) as response:
    secret_value = json.load(response)["value"]
if not secret_value:
    raise RuntimeError("Hugging Face token secret is empty")
print(secret_value, end="")
PY
)"
  export HF_TOKEN
  echo "[azureml] loaded Hugging Face token from Key Vault"
fi

export DATA_CONFIG="${DATA_CONFIG:-custom_n17}"
export BASE_MODEL="${BASE_MODEL:-nvidia/GR00T-N1.7-3B}"
export BASE_MODEL_REVISION="${BASE_MODEL_REVISION:-2fc962b973bccdd5d8ce4f67cc63b264d6886495}"
export ISAAC_GROOT_REF="${ISAAC_GROOT_REF:-23ace64f17aa5015259b8609d371eb61a357c776}"
export EMBODIMENT_TAG="${EMBODIMENT_TAG:-NEW_EMBODIMENT}"
export BATCH_SIZE="${BATCH_SIZE:-4}"
export MAX_STEPS="${MAX_STEPS:-100}"
export SAVE_STEPS="${SAVE_STEPS:-50}"
export DATALOADER_WORKERS="${DATALOADER_WORKERS:-4}"
export NUM_GPUS="${NUM_GPUS:-2}"
export RUN_ID_OVERRIDE="${RUN_ID_OVERRIDE:-training}"
export AZURE_UPLOAD="${AZURE_UPLOAD:-false}"
export MODEL_CACHE_ROOT="${MODEL_CACHE_ROOT:-}"

if [ "${AZURE_UPLOAD}" = "true" ]; then
  : "${AZUREML_MODEL_NAME:?AZUREML_MODEL_NAME is required when AZURE_UPLOAD=true}"
  cp "${TRAINING_ROOT}/utils/aml_mirror.py" /tmp/aml_mirror.py
fi

exec "${SCRIPT_DIR}/osmo-train-entry.sh"
