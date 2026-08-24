#!/usr/bin/env bash
# AzureML entry script for LeRobot evaluation
# All configuration via environment variables set by submit-azureml-lerobot-eval.sh
set -euo pipefail

echo "=== LeRobot AzureML Evaluation ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../../.." && pwd))"

# shellcheck source=training/il/scripts/lerobot/lerobot-azureml.sh
source "${REPO_ROOT}/training/il/scripts/lerobot/lerobot-azureml.sh"

ensure_lerobot_runtime "${LEROBOT_EVAL_VENV:-/opt/lerobot-eval-venv}" "${REPO_ROOT}/training/il/lerobot" av azure.ai.ml azure.identity azure.storage.blob azureml.mlflow lerobot matplotlib mlflow pyarrow

if [[ -n "${AZURE_ML_OUTPUT_eval_results:-}" ]]; then
  export OUTPUT_DIR="${AZURE_ML_OUTPUT_eval_results}"
fi

# HuggingFace auth
if [[ -n "${HF_TOKEN:-}" ]]; then
  python3 -c "import os; from huggingface_hub import login; login(token=os.environ['HF_TOKEN'], add_to_git_credential=False)"
fi

# Download model from AzureML registry if specified
if [[ -n "${AML_MODEL_NAME:-}" && "${AML_MODEL_NAME}" != "none" && -n "${AML_MODEL_VERSION:-}" && "${AML_MODEL_VERSION}" != "none" ]]; then
  echo "Downloading model from AzureML registry: ${AML_MODEL_NAME}:${AML_MODEL_VERSION}..."

  python3 "${REPO_ROOT}/evaluation/sil/scripts/download_aml_model.py"

  if [[ -f /tmp/aml_model_path.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/aml_model_path.env | xargs)
    export POLICY_REPO_ID="${AML_MODEL_PATH}"
    echo "Using AzureML model at: ${POLICY_REPO_ID}"
  else
    echo "Error: Model download did not produce path file"
    exit 1
  fi
fi

# Download dataset from Azure Blob Storage if configured
if [[ -n "${BLOB_STORAGE_ACCOUNT:-}" && "${BLOB_STORAGE_ACCOUNT}" != "none" && -n "${BLOB_PREFIX:-}" && "${BLOB_PREFIX}" != "none" ]]; then
  echo "Downloading dataset from Azure Blob: ${BLOB_STORAGE_ACCOUNT}/${BLOB_STORAGE_CONTAINER}/${BLOB_PREFIX}..."

  python3 "${REPO_ROOT}/evaluation/sil/scripts/download_blob_dataset.py"

  if [[ -f /tmp/dataset_path.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/dataset_path.env | xargs)
    echo "Dataset ready at: ${DATASET_DIR}"
  fi
fi

# Bootstrap MLflow tracking
if [[ "${MLFLOW_ENABLE:-false}" == "true" ]]; then
  echo "Configuring Azure ML MLflow tracking..."

  python3 "${REPO_ROOT}/evaluation/metrics/bootstrap_mlflow.py"

  if [[ -f /tmp/mlflow_config.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/mlflow_config.env | xargs)
  fi
fi

# Run evaluation
echo "Starting LeRobot evaluation..."
mkdir -p "${OUTPUT_DIR}"

python3 "${REPO_ROOT}/evaluation/sil/scripts/run_evaluation.py"

echo "=== Evaluation Complete ==="

# Register model to Azure ML if requested
if [[ -n "${REGISTER_MODEL:-}" && "${REGISTER_MODEL}" != "none" ]]; then
  echo "=== Registering Model to Azure ML ==="
  python3 "${REPO_ROOT}/workflows/azureml/scripts/register_model.py"
  echo "=== Model Registration Complete ==="
fi
