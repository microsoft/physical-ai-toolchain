#!/usr/bin/env bash
# Configure and launch Azure ML VLA calibration and training components.
set -euo pipefail

mode="${1:-}"
if [[ -z "${mode}" ]]; then
  echo "ERROR: component mode is required (calibrate or train)" >&2
  exit 1
fi
shift

require_environment_variables() {
  local name
  for name in "$@"; do
    if [[ -z "${!name:-}" ]]; then
      echo "ERROR: ${name} is required for ${mode}" >&2
      exit 1
    fi
  done
}

environment_value() {
  local name="$1"
  printf '%s' "${!name:-}"
}

export_parameter() {
  local target_name="$1"
  local parameter_name="AZUREML_PARAMETER_$2"
  local value
  value=$(environment_value "${parameter_name}")
  if [[ "${value}" != "none" ]]; then
    export "${target_name}=${value}"
  fi
}

export LEROBOT_PROJECT="training/vla/lerobot"

case "${mode}" in
  calibrate)
    export_parameter ADAPTER_NAME adapter_name
    export_parameter DATASET_REPO_ID dataset_repo_id
    export_parameter DATASET_REVISION dataset_revision
    export_parameter POLICY_TYPE policy_type
    export_parameter MIXED_PRECISION mixed_precision
    export_parameter POLICY_DTYPE policy_dtype
    export_parameter INIT_FROM_POLICY_HF_REPO_ID init_from_policy_hf_repo_id
    export_parameter INIT_FROM_POLICY_HF_REVISION init_from_policy_hf_revision
    export_parameter TRAIN_EXPERT_ONLY train_expert_only
    export_parameter GRADIENT_CHECKPOINTING gradient_checkpointing
    export_parameter USE_IMAGENET_STATS use_imagenet_stats
    export_parameter RENAME_MAP_B64 rename_map_b64
    export_parameter CALIBRATION_BATCH_SIZES candidate_batch_sizes
    export_parameter CALIBRATION_HEADROOM_FRACTION headroom_fraction
    export_parameter CALIBRATION_PROBE_TIMEOUT_SECONDS probe_timeout_seconds
    export_parameter CODE_REPOSITORY code_repository
    export_parameter CODE_REVISION code_revision
    export_parameter COMPUTE_TARGET compute_target
    export_parameter RUNTIME_IMAGE runtime_image

    require_environment_variables \
      ADAPTER_NAME DATASET_REPO_ID DATASET_REVISION POLICY_TYPE MIXED_PRECISION \
      INIT_FROM_POLICY_HF_REPO_ID INIT_FROM_POLICY_HF_REVISION TRAIN_EXPERT_ONLY \
      GRADIENT_CHECKPOINTING USE_IMAGENET_STATS CODE_REPOSITORY CODE_REVISION \
      COMPUTE_TARGET RUNTIME_IMAGE CALIBRATION_BATCH_SIZES \
      CALIBRATION_HEADROOM_FRACTION CALIBRATION_PROBE_TIMEOUT_SECONDS \
      AZURE_ML_OUTPUT_workload_contract AZURE_ML_OUTPUT_calibration_report

    if [[ -n "${AZURE_ML_INPUT_prepared_dataset:-}" ]]; then
      export AZURE_ML_INPUT_dataset_asset_0="${AZURE_ML_INPUT_prepared_dataset}"
      export DATASET_ASSET_COUNT=1
    fi
    export CALIBRATION_MODE=true
    CALIBRATION_WORKLOAD_OUTPUT_DIR=$(environment_value AZURE_ML_OUTPUT_workload_contract)
    CALIBRATION_OUTPUT_DIR=$(environment_value AZURE_ML_OUTPUT_calibration_report)
    export CALIBRATION_WORKLOAD_OUTPUT_DIR CALIBRATION_OUTPUT_DIR
    ;;
  train)
    export_parameter ADAPTER_NAME adapter_name
    export_parameter AZURE_SUBSCRIPTION_ID subscription_id
    export_parameter AZURE_RESOURCE_GROUP resource_group
    export_parameter AZUREML_WORKSPACE_NAME workspace_name
    export_parameter MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES mlflow_token_refresh_retries
    export_parameter MLFLOW_HTTP_REQUEST_TIMEOUT mlflow_http_request_timeout
    export_parameter DATASET_REPO_ID dataset_repo_id
    export_parameter DATASET_REVISION dataset_revision
    export_parameter POLICY_TYPE policy_type
    export_parameter INIT_FROM_POLICY_HF_REPO_ID init_from_policy_hf_repo_id
    export_parameter INIT_FROM_POLICY_HF_REVISION init_from_policy_hf_revision
    export_parameter TRAIN_EXPERT_ONLY train_expert_only
    export_parameter GRADIENT_CHECKPOINTING gradient_checkpointing
    export_parameter USE_IMAGENET_STATS use_imagenet_stats
    export_parameter TRAINING_STEPS training_steps
    export_parameter SAVE_FREQ save_freq
    export_parameter LOG_FREQ log_freq
    export_parameter JOB_NAME job_name
    export_parameter OUTPUT_DIR output_dir
    export_parameter MIXED_PRECISION mixed_precision
    export_parameter POLICY_DTYPE policy_dtype
    export_parameter AZURE_CLIENT_ID azure_client_id
    export_parameter HF_KEY_VAULT_URL hf_key_vault_url
    export_parameter HF_TOKEN_SECRET_NAME hf_token_secret_name
    export_parameter REGISTER_CHECKPOINT register_checkpoint
    export_parameter RENAME_MAP_B64 rename_map_b64
    export_parameter CODE_REPOSITORY code_repository
    export_parameter CODE_REVISION code_revision
    export_parameter COMPUTE_TARGET compute_target
    export_parameter RUNTIME_IMAGE runtime_image

    require_environment_variables \
      ADAPTER_NAME AZURE_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AZUREML_WORKSPACE_NAME \
      DATASET_REPO_ID DATASET_REVISION POLICY_TYPE INIT_FROM_POLICY_HF_REPO_ID \
      INIT_FROM_POLICY_HF_REVISION TRAIN_EXPERT_ONLY GRADIENT_CHECKPOINTING \
      USE_IMAGENET_STATS TRAINING_STEPS SAVE_FREQ LOG_FREQ JOB_NAME OUTPUT_DIR \
      MIXED_PRECISION CODE_REPOSITORY CODE_REVISION COMPUTE_TARGET RUNTIME_IMAGE \
      AZURE_ML_OUTPUT_checkpoints AZURE_ML_INPUT_calibration_report \
      AZURE_ML_INPUT_workload_contract

    TRAINING_CHECKPOINT_OUTPUT=$(environment_value AZURE_ML_OUTPUT_checkpoints)
    CALIBRATION_REPORT_DIR=$(environment_value AZURE_ML_INPUT_calibration_report)
    CALIBRATION_WORKLOAD_CONTRACT="$(environment_value AZURE_ML_INPUT_workload_contract)/workload.json"
    export TRAINING_CHECKPOINT_OUTPUT CALIBRATION_REPORT_DIR CALIBRATION_WORKLOAD_CONTRACT
    ;;
  *)
    echo "ERROR: unsupported component mode '${mode}'" >&2
    exit 1
    ;;
esac

  export VLA_MODEL_ADAPTER="${ADAPTER_NAME}"
exec bash training/il/scripts/lerobot/azureml-train-entry.sh
