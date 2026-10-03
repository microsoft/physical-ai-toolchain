#!/usr/bin/env bash
# Submit the Azure ML GPU smoke test using training/ as the code directory
# The .amlignore file controls which files are excluded from the code snapshot
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << EOF
Usage: submit-azureml-gpu-smoke.sh [OPTIONS] [-- az-ml-job-flags]

Submit a short Azure ML GPU job that proves a GPU target and the services a
training job depends on: identity and workspace access, MLflow metrics and
artifacts, checkpoints in the job output, Azure Storage uploads, and the model
registry. The job exits non-zero when any check fails.

SMOKE TEST OPTIONS:
        --instance-type NAME      Instance type to test (default: gpuspot)
        --steps N                 Training steps (default: 200)
        --checkpoint-interval N   Steps between checkpoints (default: 50)
        --model-name NAME         Registered model name (default: gpu-smoke-test)
        --skip-register-model     Skip the model registry check
        --storage-account NAME    Storage account for the upload check (default: from Terraform;
                                  empty skips the check)

AZUREML ASSET OPTIONS:
        --environment-name NAME   AzureML environment name (default: gpu-smoke-env)
        --environment-version VER Environment version (default: derived from --image)
        --image IMAGE             Container image (default: ${DEFAULT_AZUREML_SMOKE_IMAGE})
    -w, --job-file PATH           Job YAML template (default: training/smoke/workflows/azureml/gpu-smoke.yaml)

AZURE CONTEXT:
        --subscription-id ID      Azure subscription ID
        --resource-group NAME     Azure resource group
        --workspace-name NAME     Azure ML workspace
        --compute TARGET          Compute target (default: derived from the AKS cluster name)
        --experiment-name NAME    Experiment name (default: gpu-smoke)
        --job-name NAME           Job name override
        --stream                  Stream logs, then verify the job status and its checkpoints output

GENERAL:
    -h, --help                    Show this help message
        --config-preview          Print configuration and exit

Values resolved: CLI > Environment variables > Terraform outputs
EOF
}

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

ensure_ml_extension() {
  az extension show --name ml &>/dev/null ||
    fatal "Azure ML CLI extension not installed. Run: az extension add --name ml"
}

require_positive_int() {
  [[ "$2" =~ ^[1-9][0-9]*$ ]] || fatal "$1 must be a positive integer, got '$2'"
}

# Download the job's checkpoints output and report the smoke summary it contains.
verify_job() {
  local job="$1" status download_dir summary checkpoint_count summary_status

  status=$(az ml job show --name "$job" \
    --resource-group "$resource_group" --workspace-name "$workspace_name" \
    --query status -o tsv)

  download_dir=$(mktemp -d)
  if ! az ml job download --name "$job" --output-name checkpoints --download-path "$download_dir" \
    --resource-group "$resource_group" --workspace-name "$workspace_name" >/dev/null 2>&1; then
    warn "Couldn't download the checkpoints output of $job"
  fi
  summary=$(find "$download_dir" -type f -name smoke-summary.json | head -n 1)
  checkpoint_count=$(find "$download_dir" -type f -name 'checkpoint-step-*.pt' | wc -l | tr -d ' ')

  section "Smoke Test Results"
  print_kv "Job Status" "$status"
  print_kv "Checkpoints Output" "$checkpoint_count checkpoint files downloaded"
  if [[ -z "$summary" ]]; then
    rm -rf "$download_dir"
    error "smoke-summary.json not found in the checkpoints output"
    return 1
  fi

  summary_status=$(jq -r '.status' "$summary")
  print_kv "Summary Status" "$summary_status"
  jq -r '.checks[] | [.status, .name, (.error // "")] | @tsv' "$summary" |
    while IFS=$'\t' read -r check_status check_name check_error; do
      printf '  %-8s %-18s %s\n' "$check_status" "$check_name" "$check_error"
    done
  rm -rf "$download_dir"

  [[ "$status" == "Completed" && "$summary_status" == "passed" && "$checkpoint_count" -gt 0 ]]
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

# Azure ML reserves names starting with "AzureML" (any case) for curated environments.
environment_name="gpu-smoke-env"
image="${IMAGE:-$DEFAULT_AZUREML_SMOKE_IMAGE}"
environment_version="${ENVIRONMENT_VERSION:-}"
environment_version_explicit=false
[[ -n "${ENVIRONMENT_VERSION:-}" ]] && environment_version_explicit=true

job_file="$REPO_ROOT/training/smoke/workflows/azureml/gpu-smoke.yaml"
instance_type="gpuspot"
steps="200"
checkpoint_interval="50"
model_name="gpu-smoke-test"
register_model="true"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
storage_account="${AZURE_STORAGE_ACCOUNT_NAME-$(get_storage_account)}"
compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
experiment_name="gpu-smoke"
job_name=""
stream_logs=false
config_preview=false
forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                  show_help; exit 0 ;;
    --instance-type)            instance_type="$2"; shift 2 ;;
    --steps)                    steps="$2"; shift 2 ;;
    --checkpoint-interval)      checkpoint_interval="$2"; shift 2 ;;
    --model-name)               model_name="$2"; shift 2 ;;
    --skip-register-model)      register_model="false"; shift ;;
    --storage-account)          storage_account="$2"; shift 2 ;;
    --environment-name)         environment_name="$2"; shift 2 ;;
    --environment-version)      environment_version="$2"; environment_version_explicit=true; shift 2 ;;
    --image)                    image="$2"; shift 2 ;;
    -w|--job-file)              job_file="$2"; shift 2 ;;
    --subscription-id)          subscription_id="$2"; shift 2 ;;
    --resource-group)           resource_group="$2"; shift 2 ;;
    --workspace-name)           workspace_name="$2"; shift 2 ;;
    --compute)                  compute="$2"; shift 2 ;;
    --experiment-name)          experiment_name="$2"; shift 2 ;;
    --job-name)                 job_name="$2"; shift 2 ;;
    --stream)                   stream_logs=true; shift ;;
    --config-preview)           config_preview=true; shift ;;
    --)                         shift; forward_args=("$@"); break ;;
    *)                          fatal "Unknown option: $1" ;;
  esac
done

if [[ "$environment_version_explicit" != "true" ]]; then
  environment_version="$(derive_azureml_environment_version_from_image "$image")"
fi

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_positive_int "--steps" "$steps"
require_positive_int "--checkpoint-interval" "$checkpoint_interval"
[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"
[[ -n "$compute" ]] || fatal "Compute target required; pass --compute or set AZUREML_COMPUTE"
[[ -n "$instance_type" ]] || fatal "--instance-type required"

code_path="$REPO_ROOT/training"
[[ -d "$code_path/smoke" ]] || fatal "Smoke test source not found: $code_path/smoke"
[[ -f "$code_path/.amlignore" ]] || warn "No training/.amlignore found; the AML snapshot may include unrelated files"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Instance Type" "$instance_type"
  print_kv "Compute" "$compute"
  print_kv "Steps" "$steps"
  print_kv "Checkpoint Interval" "$checkpoint_interval"
  print_kv "Register Model" "$([[ "$register_model" == "true" ]] && echo "$model_name" || echo 'Skipped')"
  print_kv "Storage Check" "${storage_account:-Skipped (no storage account)}"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Experiment" "$experiment_name"
  print_kv "Job File" "$job_file"
  print_kv "Environment" "${environment_name}:${environment_version}"
  print_kv "Image" "$image"
  exit 0
fi

require_tools az jq
ensure_ml_extension
[[ -f "$job_file" ]] || fatal "Job file not found: $job_file"

#------------------------------------------------------------------------------
# Register Environment
#------------------------------------------------------------------------------

register_azureml_environment "$environment_name" "$environment_version" "$image" \
  "$resource_group" "$workspace_name" "$subscription_id"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

az_args=(
  az ml job create
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --file "$job_file"
  --set "code=$code_path"
  --set "environment=azureml:${environment_name}:${environment_version}"
  --set "compute=$compute"
  --set "resources.instance_type=$instance_type"
  --set "experiment_name=$experiment_name"
  --set "inputs.steps=$steps"
  --set "inputs.checkpoint_interval=$checkpoint_interval"
  --set "inputs.model_name=$model_name"
  --set "inputs.register_model=$register_model"
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
)
[[ -n "$storage_account" ]] && az_args+=(--set "environment_variables.AZURE_STORAGE_ACCOUNT_NAME=$storage_account")
[[ -n "$job_name" ]] && az_args+=(--set "name=$job_name")
[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
az_args+=(--query "name" -o "tsv")

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting Azure ML GPU smoke test on instance type $instance_type..."
job_result=$("${az_args[@]}") || fatal "Job submission failed"

info "Job submitted: $job_result"
info "Portal: https://ml.azure.com/runs/$job_result?wsid=/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.MachineLearningServices/workspaces/$workspace_name"

verified="not run"
if [[ "$stream_logs" == "true" ]]; then
  info "Streaming job logs (Ctrl+C to stop)..."
  az ml job stream --name "$job_result" \
    --resource-group "$resource_group" --workspace-name "$workspace_name" || true
  if verify_job "$job_result"; then
    verified="passed"
  else
    verified="failed"
  fi
else
  info "Download results: az ml job download --name $job_result --output-name checkpoints"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Job Name" "$job_result"
print_kv "Instance Type" "$instance_type"
print_kv "Compute" "$compute"
print_kv "Environment" "${environment_name}:${environment_version}"
print_kv "Workspace" "$workspace_name"
print_kv "Verification" "$verified"

[[ "$verified" != "failed" ]] || fatal "Azure ML GPU smoke test failed; see the results above"
