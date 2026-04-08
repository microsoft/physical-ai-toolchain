#!/usr/bin/env bash
# Submit Azure ML validation job using evaluation/ as the code directory
# The .amlignore file controls which files are excluded from the code snapshot
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: submit-azureml-validation.sh [OPTIONS]

Submit an Azure ML validation job to evaluate a trained model.

MODEL OPTIONS:
    --model-name NAME             Azure ML model name (default: derived from task)
    --model-version VERSION       Model version (default: latest)

AZUREML ASSET OPTIONS:
    --environment-name NAME       AzureML environment name (default: isaaclab-training-env)
    --environment-version VER     Environment version (default: 2.3.2)
    --image IMAGE                 Container image (default: nvcr.io/nvidia/isaac-lab:2.3.2)

VALIDATION OPTIONS:
    --task TASK                   Override task ID (default: from model metadata)
    --framework FRAMEWORK         Override framework (default: from model metadata)
    --eval-episodes N             Evaluation episodes (default: 100)
    --num-envs N                  Parallel environments (default: 64)
    --success-threshold F         Success threshold (default: from model metadata)
    --headless                    Run headless (default)
    --gui                         Disable headless mode

AZURE CONTEXT:
    --job-file PATH               Job YAML template (default: evaluation/sil/workflows/azureml/validate.yaml)
    --compute TARGET              Compute target override
    --instance-type TYPE          Instance type (default: gpuspot)
    --experiment-name NAME        Experiment name override
    --job-name NAME               Job name override
    --stream                      Stream logs after submission

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

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

environment_name="isaaclab-training-env"
environment_version="2.3.2"
image="nvcr.io/nvidia/isaac-lab:2.3.2"

model_name=""
model_version="latest"

task="${TASK:-Isaac-Velocity-Rough-Anymal-C-v0}"
framework=""
episodes=100
num_envs=64
threshold=""
headless="true"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

job_file="$REPO_ROOT/evaluation/sil/workflows/azureml/validate.yaml"
compute="${AZUREML_COMPUTE:-$(get_compute_target)}"
instance_type="gpuspot"
experiment_name=""
job_name=""
stream_logs=false
config_preview=false

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --model-name)           model_name="$2"; shift 2 ;;
    --model-version)        model_version="$2"; shift 2 ;;
    --environment-name)     environment_name="$2"; shift 2 ;;
    --environment-version)  environment_version="$2"; shift 2 ;;
    --image)                image="$2"; shift 2 ;;
    --task)                 task="$2"; shift 2 ;;
    --framework)            framework="$2"; shift 2 ;;
    --eval-episodes)        episodes="$2"; shift 2 ;;
    --num-envs)             num_envs="$2"; shift 2 ;;
    --success-threshold)    threshold="$2"; shift 2 ;;
    --headless)             headless="true"; shift ;;
    --gui)                  headless="false"; shift ;;
    --job-file)             job_file="$2"; shift 2 ;;
    --compute)              compute="$2"; shift 2 ;;
    --instance-type)        instance_type="$2"; shift 2 ;;
    --experiment-name)      experiment_name="$2"; shift 2 ;;
    --job-name)             job_name="$2"; shift 2 ;;
    --stream)               stream_logs=true; shift ;;
    --config-preview)       config_preview=true; shift ;;
    --subscription-id)      subscription_id="$2"; shift 2 ;;
    --resource-group)       resource_group="$2"; shift 2 ;;
    --workspace-name)       workspace_name="$2"; shift 2 ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools az jq
ensure_ml_extension

[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"
[[ -f "$job_file" ]] || fatal "Job file not found: $job_file"

if [[ -z "$model_name" ]]; then
  model_name="$(derive_model_name "$task")"
  info "Auto-derived model name: $model_name"
fi

code_path="$REPO_ROOT/evaluation"
[[ -d "$code_path/sil" ]] || fatal "SIL evaluation source not found: $code_path/sil"
[[ -f "$code_path/.amlignore" ]] || warn "No evaluation/.amlignore found; the AML snapshot may include unrelated files"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Model" "${model_name}:${model_version}"
  print_kv "Task" "$task"
  print_kv "Framework" "${framework:-<from model>}"
  print_kv "Episodes" "$episodes"
  print_kv "Num Envs" "$num_envs"
  print_kv "Threshold" "${threshold:-<from model>}"
  print_kv "Headless" "$headless"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Compute" "${compute:-<not set>}"
  print_kv "Instance Type" "$instance_type"
  print_kv "Job File" "$job_file"
  print_kv "Environment" "${environment_name}:${environment_version}"
  exit 0
fi

#------------------------------------------------------------------------------
# Resolve Model Version
#------------------------------------------------------------------------------

if [[ "$model_version" == "latest" ]]; then
  info "Resolving latest version for model: $model_name"
  model_version=$(az ml model list \
    --name "$model_name" \
    --resource-group "$resource_group" \
    --workspace-name "$workspace_name" \
    --query "[0].version" -o tsv 2>/dev/null || echo "")
  [[ -n "$model_version" ]] || fatal "Could not find model: $model_name"
  info "Using version: $model_version"
fi

model_uri="azureml:${model_name}:${model_version}"

#------------------------------------------------------------------------------
# Fetch Model Metadata
#------------------------------------------------------------------------------

info "Fetching model metadata..."
model_json=$(az ml model show \
  --name "$model_name" \
  --version "$model_version" \
  --resource-group "$resource_group" \
  --workspace-name "$workspace_name" \
  -o json 2>/dev/null || echo "{}")

[[ -z "$task" ]] && task=$(echo "$model_json" | jq -r '.tags.task // "auto"')
[[ -z "$framework" ]] && framework=$(echo "$model_json" | jq -r '.tags.framework // "auto"')
[[ -z "$threshold" ]] && threshold=$(echo "$model_json" | jq -r '.properties.success_threshold // "-1.0"')

#------------------------------------------------------------------------------
# Register Environment
#------------------------------------------------------------------------------

register_environment "$environment_name" "$environment_version" "$image" \
  "$resource_group" "$workspace_name"

info "Code path: $code_path"
info "Environment: ${environment_name}:${environment_version}"

#------------------------------------------------------------------------------
# Build Submission Command
#------------------------------------------------------------------------------

info "Preparing validation job..."
info "  Model: $model_uri"
info "  Task: $task"
info "  Framework: $framework"
info "  Episodes: $episodes"
info "  Threshold: $threshold"

az_args=(
  az ml job create
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --file "$job_file"
  --set "code=$code_path"
  --set "environment=azureml:${environment_name}:${environment_version}"
  --set "inputs.trained_model.path=$model_uri"
)

[[ -n "$compute" ]] && az_args+=(--set "compute=$compute")
[[ -n "$instance_type" ]] && az_args+=(--set "resources.instance_type=$instance_type")
[[ -n "$experiment_name" ]] && az_args+=(--set "experiment_name=$experiment_name")
[[ -n "$job_name" ]] && az_args+=(--set "name=$job_name")

# Build validation command (paths relative to code root)
cmd="--model-path \${{inputs.trained_model}}"
cmd="$cmd --eval-episodes \${{inputs.eval_episodes}}"
cmd="$cmd --num-envs \${{inputs.num_envs}}"
cmd="$cmd --task \${{inputs.task}}"
cmd="$cmd --framework \${{inputs.framework}}"
cmd="$cmd --success-threshold \${{inputs.success_threshold}}"

[[ "$headless" == "true" ]] && cmd="$cmd --headless"

# AML snapshots evaluation/ as the code root, so recreate the top-level evaluation path
# expected by the shell entrypoint and Python imports inside the job container.
az_args+=(
  --set "command=if [ ! -e evaluation ]; then ln -s . evaluation; fi && bash evaluation/sil/validate.sh $cmd"
  --set "inputs.task=${task:-auto}"
  --set "inputs.framework=${framework:-auto}"
  --set "inputs.success_threshold=${threshold:--1.0}"
  --set "inputs.eval_episodes=$episodes"
  --set "inputs.num_envs=$num_envs"
  --query "name" -o "tsv"
)

#------------------------------------------------------------------------------
# Submit Job
#------------------------------------------------------------------------------

info "Submitting validation job..."
job_result=$("${az_args[@]}") || fatal "Job submission failed"
[[ -n "$job_result" ]] || fatal "Job submission failed - no job name returned"

info "Job submitted: $job_result"
info "Portal: https://ml.azure.com/runs/$job_result?wsid=/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.MachineLearningServices/workspaces/$workspace_name"

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
print_kv "Model" "$model_uri"
print_kv "Task" "$task"
print_kv "Framework" "${framework:-auto}"
print_kv "Episodes" "$episodes"
print_kv "Threshold" "${threshold:--1.0}"
print_kv "Compute" "${compute:-<not set>}"
print_kv "Instance Type" "$instance_type"
print_kv "Workspace" "$workspace_name"
