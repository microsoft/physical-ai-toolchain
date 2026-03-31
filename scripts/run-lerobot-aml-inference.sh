#!/usr/bin/env bash
# Download a LeRobot checkpoint from Azure ML model registry and run inference
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

source "$REPO_ROOT/deploy/002-setup/lib/common.sh"
source "$SCRIPT_DIR/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/deploy/001-iac" 2>/dev/null || true

# Source .env file if present
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: run-lerobot-aml-inference.sh [OPTIONS]

Download a LeRobot checkpoint from the Azure ML model registry and run
local inference using test-lerobot-inference.py.

MODEL SOURCE (required):
    -m, --model-name NAME         AML model registry name (e.g., hve-robo-act-model)
    -v, --model-version VERSION   Model version number (e.g., 4)

DATASET (required):
    -d, --dataset-dir DIR         Path to LeRobot v3 dataset root

INFERENCE OPTIONS:
    -e, --episode N               Episode index (default: 0)
    --start-frame N               Starting frame index (default: 0)
    --num-steps N                 Number of inference steps (default: 30)
    --device DEVICE               Device for inference: cuda, cpu, mps (default: cuda)
    -o, --output PATH             Save predictions to .npz file

DOWNLOAD OPTIONS:
    --download-dir DIR            Local directory for model download
                                  (default: /tmp/lerobot-aml-models)
    --force                       Re-download even if model already exists locally

AZURE CONTEXT:
    --subscription-id ID          Azure subscription ID
    --resource-group NAME         Azure resource group
    --workspace-name NAME         Azure ML workspace name

OTHER:
    --config-preview              Print configuration and exit
    -h, --help                    Show this help message

EXAMPLES:
    # Download model v4 and run inference on local dataset
    run-lerobot-aml-inference.sh \
      -m hve-robo-act-model -v 4 \
      -d /path/to/houston_lerobot_fixed

    # Run on CPU with custom output
    run-lerobot-aml-inference.sh \
      -m houston-ur10e-act -v 1 \
      -d ./tmp/houston_lerobot \
      --device cpu \
      --num-steps 50 \
      -o predictions.npz

    # Force re-download a specific version
    run-lerobot-aml-inference.sh \
      -m hve-robo-act-model -v 4 \
      -d ./data/houston_lerobot_fixed \
      --force
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

model_name=""
model_version=""
dataset_dir=""
episode=0
start_frame=0
num_steps=30
device="cuda"
output=""
download_dir="/tmp/lerobot-aml-models"
force_download=false
config_preview=false

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    -m|--model-name)        model_name="$2"; shift 2 ;;
    -v|--model-version)     model_version="$2"; shift 2 ;;
    -d|--dataset-dir)       dataset_dir="$2"; shift 2 ;;
    -e|--episode)           episode="$2"; shift 2 ;;
    --start-frame)          start_frame="$2"; shift 2 ;;
    --num-steps)            num_steps="$2"; shift 2 ;;
    --device)               device="$2"; shift 2 ;;
    -o|--output)            output="$2"; shift 2 ;;
    --download-dir)         download_dir="$2"; shift 2 ;;
    --force)                force_download=true; shift ;;
    --subscription-id)      subscription_id="$2"; shift 2 ;;
    --resource-group)       resource_group="$2"; shift 2 ;;
    --workspace-name)       workspace_name="$2"; shift 2 ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools python3 az

[[ -z "$model_name" ]]    && fatal "--model-name is required"
[[ -z "$model_version" ]] && fatal "--model-version is required"
[[ -z "$dataset_dir" ]]   && fatal "--dataset-dir is required"
[[ -d "$dataset_dir" ]]   || fatal "Dataset directory not found: $dataset_dir"

[[ -z "$subscription_id" ]] && fatal "Azure subscription ID required (set AZURE_SUBSCRIPTION_ID or deploy infra)"
[[ -z "$resource_group" ]]  && fatal "Azure resource group required (set AZURE_RESOURCE_GROUP or deploy infra)"
[[ -z "$workspace_name" ]]  && fatal "Azure ML workspace name required (set AZUREML_WORKSPACE_NAME or deploy infra)"

#------------------------------------------------------------------------------
# Config Preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Model" "${model_name}:${model_version}"
  print_kv "Dataset Dir" "$dataset_dir"
  print_kv "Episode" "$episode"
  print_kv "Start Frame" "$start_frame"
  print_kv "Num Steps" "$num_steps"
  print_kv "Device" "$device"
  print_kv "Output" "${output:-<none>}"
  print_kv "Download Dir" "$download_dir"
  print_kv "Force Download" "$force_download"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  exit 0
fi

#------------------------------------------------------------------------------
# Download Model from Azure ML
#------------------------------------------------------------------------------

section "Download Model"
info "Model: ${model_name}:${model_version}"

model_local_dir="${download_dir}/${model_name}/v${model_version}"

if [[ -d "$model_local_dir" && "$force_download" == "false" ]]; then
  # Verify the cached download has model weights
  if compgen -G "${model_local_dir}/*.safetensors" > /dev/null 2>&1 || \
     compgen -G "${model_local_dir}/*.bin" > /dev/null 2>&1; then
    info "Model already downloaded at: $model_local_dir"
  else
    info "Cached directory exists but has no model weights, re-downloading..."
    rm -rf "$model_local_dir"
  fi
fi

if [[ ! -d "$model_local_dir" ]] || [[ "$force_download" == "true" ]]; then
  mkdir -p "$model_local_dir"
  info "Downloading from Azure ML model registry..."

  az ml model download \
    --name "$model_name" \
    --version "$model_version" \
    --download-path "$model_local_dir" \
    --subscription "$subscription_id" \
    --resource-group "$resource_group" \
    --workspace-name "$workspace_name"

  # az ml model download creates a subdirectory with the model name;
  # flatten if the checkpoint files are nested one level deep
  nested_dir="${model_local_dir}/${model_name}"
  if [[ -d "$nested_dir" ]]; then
    # Move contents up and remove the nested directory
    find "$nested_dir" -mindepth 1 -maxdepth 1 -exec mv -f {} "$model_local_dir/" \;
    rmdir "$nested_dir" 2>/dev/null || true
  fi

  info "Downloaded to: $model_local_dir"
fi

# Verify model files exist
if ! compgen -G "${model_local_dir}/*.safetensors" > /dev/null 2>&1 && \
   ! compgen -G "${model_local_dir}/*.bin" > /dev/null 2>&1; then
  # Check one level deeper (pretrained_model subdirectory)
  for subdir in "$model_local_dir"/*/; do
    if compgen -G "${subdir}*.safetensors" > /dev/null 2>&1 || \
       compgen -G "${subdir}*.bin" > /dev/null 2>&1; then
      model_local_dir="$subdir"
      info "Using model weights from: $model_local_dir"
      break
    fi
  done
fi

if ! compgen -G "${model_local_dir}/*.safetensors" > /dev/null 2>&1 && \
   ! compgen -G "${model_local_dir}/*.bin" > /dev/null 2>&1; then
  fatal "No model weights (.safetensors or .bin) found in $model_local_dir"
fi

# List downloaded files
info "Model contents:"
ls -lh "$model_local_dir"

#------------------------------------------------------------------------------
# Run Inference
#------------------------------------------------------------------------------

section "Run Inference"
info "Policy: $model_local_dir"
info "Dataset: $dataset_dir"
info "Episode: $episode, Steps: $num_steps, Device: $device"

inference_args=(
  python3 "$SCRIPT_DIR/test-lerobot-inference.py"
  --policy-repo "$model_local_dir"
  --dataset-dir "$dataset_dir"
  --episode "$episode"
  --start-frame "$start_frame"
  --num-steps "$num_steps"
  --device "$device"
)

[[ -n "$output" ]] && inference_args+=(--output "$output")

"${inference_args[@]}"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Summary"
print_kv "Model" "${model_name}:${model_version}"
print_kv "Local Path" "$model_local_dir"
print_kv "Dataset" "$dataset_dir"
print_kv "Episode" "$episode"
print_kv "Steps" "$num_steps"
print_kv "Device" "$device"
[[ -n "$output" ]] && print_kv "Output" "$output"
info "Inference complete"
