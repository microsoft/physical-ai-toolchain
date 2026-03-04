#!/bin/bash
# AzureML entry script for LeRobot inference/evaluation
# All configuration via environment variables set by submit-azureml-lerobot-inference.sh
set -euo pipefail

echo "=== LeRobot AzureML Inference ==="

# Dependencies are pre-installed in the container image.
# If running on a non-prebaked image, uncomment the following:
# apt-get update -qq && apt-get install -y -qq ffmpeg git build-essential unzip > /dev/null 2>&1
# pip install --quiet uv
# uv pip install --system "lerobot>=0.3.0,<0.4.0" pyarrow azure-storage-blob azure-identity azure-ai-ml matplotlib
# uv pip install --system azureml-mlflow "mlflow>=2.8.0,<3.0.0"

# HuggingFace auth
if [[ -n "${HF_TOKEN:-}" ]]; then
  python3 -c "from huggingface_hub import login; login(token='${HF_TOKEN}', add_to_git_credential=False)"
fi

# Download model from AzureML registry if specified
if [[ -n "${AML_MODEL_NAME:-}" && "${AML_MODEL_NAME}" != "none" && -n "${AML_MODEL_VERSION:-}" && "${AML_MODEL_VERSION}" != "none" ]]; then
  echo "Downloading model from AzureML registry: ${AML_MODEL_NAME}:${AML_MODEL_VERSION}..."

  python3 /tmp/scripts/download_aml_model.py

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

  python3 /tmp/scripts/download_blob_dataset.py

  if [[ -f /tmp/dataset_path.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/dataset_path.env | xargs)
    echo "Dataset ready at: ${DATASET_DIR}"
  fi
fi

# Bootstrap MLflow tracking
if [[ "${MLFLOW_ENABLE:-false}" == "true" ]]; then
  echo "Configuring Azure ML MLflow tracking..."

  python3 /tmp/scripts/bootstrap_mlflow.py

  if [[ -f /tmp/mlflow_config.env ]]; then
    # shellcheck disable=SC2046
    export $(cat /tmp/mlflow_config.env | xargs)
  fi
fi

# Run evaluation
echo "Starting LeRobot evaluation..."
mkdir -p "${OUTPUT_DIR}"

python3 /tmp/scripts/run_evaluation.py

echo "=== Evaluation Complete ==="

# Register model to Azure ML if requested
if [[ -n "${REGISTER_MODEL:-}" && "${REGISTER_MODEL}" != "none" ]]; then
  echo "=== Registering Model to Azure ML ==="
  python3 /tmp/scripts/register_model.py
  echo "=== Model Registration Complete ==="
fi
