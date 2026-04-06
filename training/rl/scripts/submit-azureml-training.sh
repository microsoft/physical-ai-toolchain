#!/usr/bin/env bash
# Submit Azure ML training job using training/rl/ as the code directory
# The .amlignore file controls which files are excluded from the code snapshot
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

# shellcheck source=../../../scripts/lib/common.sh
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: submit-azureml-training.sh [OPTIONS] [-- az-ml-job-flags]

Submit an Azure ML training job with argument parity to the OSMO workflow.

AZUREML ASSET OPTIONS:
    --environment-name NAME       AzureML environment name (default: isaaclab-training-env)
    --environment-version VER     Environment version (default: 2.3.2)
    --image IMAGE                 Container image (default: nvcr.io/nvidia/isaac-lab:2.3.2)
    --assets-only                 Register environment without submitting job

TRAINING OPTIONS:
    -w, --job-file PATH           Job YAML template (default: training/rl/workflows/azureml/train.yaml)
    -t, --task NAME               IsaacLab task (default: Isaac-Velocity-Rough-Anymal-C-v0)
    -n, --num-envs COUNT          Number of environments (default: 2048)
    -m, --max-iterations N        Maximum iterations (empty to unset)
    -c, --checkpoint-uri URI      MLflow checkpoint artifact URI
    -M, --checkpoint-mode MODE    from-scratch, warm-start, resume, fresh (default: from-scratch)
    -r, --register-checkpoint ID  Model name for checkpoint registration
        --skip-register-checkpoint  Skip automatic model registration
        --headless                Force headless rendering (default)
        --gui                     Disable headless flag
    -s, --run-smoke-test          Run smoke test before submitting
        --mode MODE               Execution mode (default: train)

AZURE CONTEXT:
        --subscription-id ID      Azure subscription ID
        --resource-group NAME     Azure resource group
        --workspace-name NAME     Azure ML workspace
        --compute TARGET          Compute target override
        --instance-type NAME      Instance type (default: gpuspot)
        --experiment-name NAME    Experiment name override
        --job-name NAME           Job name override
        --display-name NAME       Display name override
        --stream                  Stream logs after submission

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

derive_model_name() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

normalize_checkpoint_mode() {
  local mode="$1"
  [[ -z "$mode" ]] && { echo "from-scratch"; return; }
  lowered=$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')
  case "$lowered" in
    from-scratch|warm-start|resume) echo "$lowered" ;;
    fresh) echo "from-scratch" ;;
    *) fatal "Unsupported checkpoint mode: $mode" ;;
  esac
}

normalize_bool() {
  local val="$1"
  lowered=$(printf '%s' "$val" | tr '[:upper:]' '[:lower:]')
  case "$lowered" in
    1|true|yes|on) echo "true" ;;
    *) echo "false" ;;
  esac
}

register_environment() {
  local name="$1" version="$2" image="$3" rg="$4" ws="$5"
  local env_file
  env_file=$(mktemp)

  cat >"$env_file" <<EOF
\$schema: https://azuremlschemas.azureedge.net/latest/environment.schema.json
name: $name
version: $version
image: $image
EOF

  info "Publishing AzureML environment ${name}:${version}"
  az ml environment create --file "$env_file" \
    --name "$name" --version "$version" \
    --resource-group "$rg" --workspace-name "$ws" >/dev/null
  rm -f "$env_file"
}

run_smoke_test() {
  local python_bin="${PYTHON:-python3}"
  command -v "$python_bin" &>/dev/null || python_bin="python"

  info "Running Azure connectivity smoke test..."
  local pythonpath="$REPO_ROOT"
  [[ -n "${PYTHONPATH:-}" ]] && pythonpath="${pythonpath}:${PYTHONPATH}"

  PYTHONPATH="$pythonpath" "$python_bin" -m training.rl.scripts.smoke_test_azure ||
    fatal "Smoke test failed; aborting submission"
  info "Smoke test passed"
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

environment_name="isaaclab-training-env"
environment_version="2.3.2"
image="nvcr.io/nvidia/isaac-lab:2.3.2"
assets_only=false

job_file="$REPO_ROOT/training/rl/workflows/azureml/train.yaml"
mode="train"
task="${TASK:-Isaac-Velocity-Rough-Anymal-C-v0}"
num_envs="${NUM_ENVS:-2048}"
max_iterations="${MAX_ITERATIONS:-}"
checkpoint_uri="${CHECKPOINT_URI:-}"
checkpoint_mode="${CHECKPOINT_MODE:-from-scratch}"
register_checkpoint="${REGISTER_CHECKPOINT:-}"
skip_register=false
run_smoke="${RUN_AZURE_SMOKE_TEST:-0}"
headless="true"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
mlflow_retries="${MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES:-3}"
mlflow_timeout="${MLFLOW_HTTP_REQUEST_TIMEOUT:-60}"

compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
instance_type="gpuspot"
experiment_name=""
job_name=""
display_name=""
stream_logs=false
config_preview=false
forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                  show_help; exit 0 ;;
    --environment-name)         environment_name="$2"; shift 2 ;;
    --environment-version)      environment_version="$2"; shift 2 ;;
    --image|-i)                 image="$2"; shift 2 ;;
    --assets-only)              assets_only=true; shift ;;
    -w|--job-file)              job_file="$2"; shift 2 ;;
    -t|--task)                  task="$2"; shift 2 ;;
    -n|--num-envs)              num_envs="$2"; shift 2 ;;
    -m|--max-iterations)        max_iterations="$2"; shift 2 ;;
    -c|--checkpoint-uri)        checkpoint_uri="$2"; shift 2 ;;
    -M|--checkpoint-mode)       checkpoint_mode="$2"; shift 2 ;;
    -r|--register-checkpoint)   register_checkpoint="$2"; shift 2 ;;
    --skip-register-checkpoint) skip_register=true; shift ;;
    -s|--run-smoke-test)        run_smoke="1"; shift ;;
    --headless)                 headless="true"; shift ;;
    --gui|--no-headless)        headless="false"; shift ;;
    --mode)                     mode="$2"; shift 2 ;;
    --subscription-id)          subscription_id="$2"; shift 2 ;;
    --resource-group)           resource_group="$2"; shift 2 ;;
    --workspace-name)           workspace_name="$2"; shift 2 ;;
    --mlflow-token-retries)     mlflow_retries="$2"; shift 2 ;;
    --mlflow-http-timeout)      mlflow_timeout="$2"; shift 2 ;;
    --experiment-name)          experiment_name="$2"; shift 2 ;;
    --compute)                  compute="$2"; shift 2 ;;
    --instance-type)            instance_type="$2"; shift 2 ;;
    --job-name)                 job_name="$2"; shift 2 ;;
    --display-name)             display_name="$2"; shift 2 ;;
    --stream)                   stream_logs=true; shift ;;
    --config-preview)           config_preview=true; shift ;;
    --)                         shift; forward_args=("$@"); break ;;
    *)                          fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools az
ensure_ml_extension

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

checkpoint_mode="$(normalize_checkpoint_mode "$checkpoint_mode")"
run_smoke="$(normalize_bool "$run_smoke")"

if [[ "$skip_register" == "false" && -z "$register_checkpoint" ]]; then
  register_checkpoint="$(derive_model_name "$task")"
  info "Auto-derived model name: $register_checkpoint"
fi

code_path="$REPO_ROOT/training"
[[ -d "$code_path/rl" ]] || fatal "RL training source not found: $code_path/rl"
[[ -f "$code_path/.amlignore" ]] || warn "No training/.amlignore found; the AML snapshot may include unrelated files"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Task" "$task"
  print_kv "Mode" "$mode"
  print_kv "Num Envs" "$num_envs"
  print_kv "Max Iterations" "${max_iterations:-<not set>}"
  print_kv "Checkpoint Mode" "$checkpoint_mode"
  print_kv "Checkpoint URI" "${checkpoint_uri:-<not set>}"
  print_kv "Register Model" "$([[ "$skip_register" == "true" ]] && echo 'Skipped' || echo "${register_checkpoint:-<auto>}")"
  print_kv "Headless" "$headless"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Compute" "${compute:-<not set>}"
  print_kv "Instance Type" "$instance_type"
  print_kv "Job File" "$job_file"
  print_kv "Environment" "${environment_name}:${environment_version}"
  print_kv "Image" "$image"
  exit 0
fi

#------------------------------------------------------------------------------
# Register Environment
#------------------------------------------------------------------------------

register_environment "$environment_name" "$environment_version" "$image" \
  "$resource_group" "$workspace_name"

info "Code path: $code_path (training/ contents only)"
info "Environment: ${environment_name}:${environment_version}"

if [[ "$assets_only" == "true" ]]; then
  info "Assets prepared; skipping job submission per --assets-only"
  exit 0
fi

#------------------------------------------------------------------------------
# Pre-submission Checks
#------------------------------------------------------------------------------

[[ -f "$job_file" ]] || fatal "Job file not found: $job_file"
[[ "$run_smoke" == "true" ]] && run_smoke_test

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
)

[[ -n "$compute" ]] && az_args+=(--set "compute=$compute")
[[ -n "$instance_type" ]] && az_args+=(--set "resources.instance_type=$instance_type")
[[ -n "$experiment_name" ]] && az_args+=(--set "experiment_name=$experiment_name")
[[ -n "$job_name" ]] && az_args+=(--set "name=$job_name")
[[ -n "$display_name" ]] && az_args+=(--set "display_name=$display_name")

# Build training command (paths relative to code root)
cmd="--mode \${{inputs.mode}} --checkpoint-mode \${{inputs.checkpoint_mode}}"

if [[ -n "$task" ]]; then
  cmd="$cmd --task \${{inputs.task}}"
  az_args+=(--set "inputs.task=$task")
fi

if [[ -n "$num_envs" ]]; then
  cmd="$cmd --num_envs \${{inputs.num_envs}}"
  az_args+=(--set "inputs.num_envs=$num_envs")
fi

if [[ -n "$max_iterations" ]]; then
  cmd="$cmd --max_iterations \${{inputs.max_iterations}}"
  az_args+=(--set "inputs.max_iterations=$max_iterations")
fi

if [[ -n "$checkpoint_uri" ]]; then
  cmd="$cmd --checkpoint-uri \${{inputs.checkpoint_uri}}"
  az_args+=(--set "inputs.checkpoint_uri=$checkpoint_uri")
fi

if [[ "$skip_register" == "false" && -n "$register_checkpoint" ]]; then
  cmd="$cmd --register-checkpoint \${{inputs.register_checkpoint}}"
  az_args+=(--set "inputs.register_checkpoint=$register_checkpoint")
fi

[[ "$headless" == "true" ]] && cmd="$cmd --headless"

# AML snapshots training/ as the code root, so recreate the top-level training path
# expected by the shell entrypoint and Python imports inside the job container.
az_args+=(--set "command=if [ ! -e training ]; then ln -s . training; fi && bash training/rl/scripts/train.sh $cmd")

# Input values
az_args+=(
  --set "inputs.mode=$mode"
  --set "inputs.checkpoint_mode=$checkpoint_mode"
  --set "inputs.headless=$headless"
  --set "inputs.subscription_id=$subscription_id"
  --set "inputs.resource_group=$resource_group"
  --set "inputs.workspace_name=$workspace_name"
  --set "inputs.run_azure_smoke_test=$run_smoke"
  --set "inputs.mlflow_token_refresh_retries=$mlflow_retries"
  --set "inputs.mlflow_http_request_timeout=$mlflow_timeout"
)

# Environment variables
az_args+=(
  --set "environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "environment_variables.RUN_AZURE_SMOKE_TEST=$run_smoke"
  --set "environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
)

[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
az_args+=(--query "name" -o "tsv")

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting AzureML job..."
job_result=$("${az_args[@]}") || fatal "Job submission failed"

info "Job submitted: $job_result"
info "Portal: https://ml.azure.com/runs/$job_result?wsid=/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.MachineLearningServices/workspaces/$workspace_name"
info "Download: az ml job download --name $job_result --output-name checkpoints"

if [[ "$stream_logs" == "true" ]]; then
  info "Streaming job logs (Ctrl+C to stop)..."
  az ml job stream --name "$job_result" \
    --resource-group "$resource_group" --workspace-name "$workspace_name" || true
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Job Name" "$job_result"
print_kv "Task" "$task"
print_kv "Mode" "$mode"
print_kv "Num Envs" "$num_envs"
print_kv "Checkpoint Mode" "$checkpoint_mode"
print_kv "Compute" "${compute:-<not set>}"
print_kv "Instance Type" "$instance_type"
print_kv "Environment" "${environment_name}:${environment_version}"
print_kv "Workspace" "$workspace_name"
