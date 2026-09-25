#!/usr/bin/env bash
# Submit a bounded CPU-only smoke job to Azure ML attached Kubernetes compute.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

GENERATED_ROOT="$REPO_ROOT/infrastructure/setup/generated"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit a bounded CPU-only command job to an existing Azure ML workspace and
attached Kubernetes compute target.

OPTIONS:
    -h, --help                       Show this help message
    --subscription-id ID             Azure ML subscription ID (required)
    --resource-group NAME            Azure ML resource group (required)
    --workspace-name NAME            Azure ML workspace name (required)
    --compute-name NAME              Attached compute name (required)
    --instance-type NAME             CPU InstanceType name (required)
    --environment REF                Versioned azureml:NAME:VERSION reference (required)
    --bundle-dir DIR                 Generated environment bundle (required)
    --job-name NAME                  Job name (default: timestamped)
    --config-preview                 Print configuration and exit

EXAMPLES:
    $(basename "$0") \
      --subscription-id <subscription-id> \
      --resource-group <resource-group> \
      --workspace-name <workspace-name> \
      --compute-name <compute-name> \
      --instance-type <cpu-instance-type> \
      --environment azureml:<environment-name>:<version> \
      --bundle-dir infrastructure/setup/generated/dev-001 \
      --config-preview
EOF
}

render_job_spec() {
  jq -n \
    --arg job_name "$job_name" \
    --arg environment "$environment_ref" \
    --arg compute "azureml:$compute_name" \
    --arg instance_type "$instance_type" \
    '{
      "$schema": "https://azuremlschemas.azureedge.net/latest/commandJob.schema.json",
      "type": "command",
      "name": $job_name,
      "display_name": "Physical AI CPU smoke",
      "description": "Validate CPU job execution on attached Kubernetes compute.",
      "experiment_name": "physical-ai-cpu-smoke",
      "environment": $environment,
      "compute": $compute,
      "resources": {
        "instance_count": 1,
        "instance_type": $instance_type
      },
      "outputs": {
        "smoke": {
          "type": "uri_folder",
          "mode": "rw_mount"
        }
      },
      "limits": {
        "timeout": 300
      },
      "command": "set -eu; architecture=$(uname -m); processors=$(getconf _NPROCESSORS_ONLN); { echo cpu-smoke-ok; echo \"$architecture\"; echo \"$processors\"; } | tee ${{outputs.smoke}}/result.txt"
    }'
}

subscription_id="${AZUREML_SUBSCRIPTION_ID:-}"
resource_group="${AZUREML_RESOURCE_GROUP:-}"
workspace_name="${AZUREML_WORKSPACE_NAME:-}"
compute_name="${AZUREML_COMPUTE_NAME:-}"
instance_type="${AZUREML_CPU_INSTANCE_TYPE:-}"
environment_ref="${AZUREML_CPU_SMOKE_ENVIRONMENT:-}"
bundle_dir="${ENVIRONMENT_BUNDLE_DIR:-}"
job_name="${AZUREML_CPU_SMOKE_JOB_NAME:-physical-ai-cpu-smoke-$(date -u +%Y%m%d%H%M%S)}"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    --subscription-id)     subscription_id="$2"; shift 2 ;;
    --resource-group)      resource_group="$2"; shift 2 ;;
    --workspace-name)      workspace_name="$2"; shift 2 ;;
    --compute-name)        compute_name="$2"; shift 2 ;;
    --instance-type)       instance_type="$2"; shift 2 ;;
    --environment)         environment_ref="$2"; shift 2 ;;
    --bundle-dir)          bundle_dir="$2"; shift 2 ;;
    --job-name)            job_name="$2"; shift 2 ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq

[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$workspace_name" ]] || fatal "--workspace-name is required"
[[ -n "$compute_name" ]] || fatal "--compute-name is required"
[[ -n "$instance_type" ]] || fatal "--instance-type is required"
[[ -n "$environment_ref" ]] || fatal "--environment is required"
[[ -n "$bundle_dir" ]] || fatal "--bundle-dir is required"
[[ "$environment_ref" =~ ^azureml:[A-Za-z0-9._-]+:[A-Za-z0-9._-]+$ ]] || \
  fatal "--environment must use azureml:NAME:VERSION"
[[ "$environment_ref" != *":latest" ]] || fatal "--environment must use an immutable version"
[[ "$instance_type" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || \
  fatal "--instance-type must be a valid lowercase Kubernetes resource name"
[[ "$job_name" =~ ^[A-Za-z0-9]([-A-Za-z0-9_]*[A-Za-z0-9])?$ ]] || \
  fatal "--job-name contains unsupported characters"

if [[ "$bundle_dir" != /* ]]; then
  bundle_dir="$REPO_ROOT/$bundle_dir"
fi
[[ "$bundle_dir" == "$GENERATED_ROOT/"* ]] || \
  fatal "--bundle-dir must be under infrastructure/setup/generated"

job_spec_file="$bundle_dir/azureml-cpu-smoke-job.json"
job_result_file="$bundle_dir/azureml-cpu-smoke-result.json"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Compute" "$compute_name"
  print_kv "Instance Type" "$instance_type"
  print_kv "Environment" "$environment_ref"
  print_kv "Job Name" "$job_name"
  print_kv "Job Specification" "$job_spec_file"
  print_kv "Job Result" "$job_result_file"
  render_job_spec | jq -e '
    .type == "command" and
    .resources.instance_count == 1 and
    (.resources | has("instance_type")) and
    .outputs.smoke.type == "uri_folder" and
    .outputs.smoke.mode == "rw_mount" and
    (.command | contains("cpu-smoke-ok"))
  ' > /dev/null
  print_kv "Job Spec Validation" "Passed"
  exit 0
fi

#------------------------------------------------------------------------------
# Validate Azure ML Target
#------------------------------------------------------------------------------
section "Validate Azure ML Target"

az account show --subscription "$subscription_id" --output none
az ml workspace show \
  --name "$workspace_name" \
  --resource-group "$resource_group" \
  --subscription "$subscription_id" \
  --output none
az ml compute show \
  --name "$compute_name" \
  --resource-group "$resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$subscription_id" \
  --output none

environment_name="${environment_ref#azureml:}"
environment_version="${environment_name##*:}"
environment_name="${environment_name%%:*}"
az ml environment show \
  --name "$environment_name" \
  --version "$environment_version" \
  --resource-group "$resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$subscription_id" \
  --output none

#------------------------------------------------------------------------------
# Render and Submit CPU Smoke Job
#------------------------------------------------------------------------------
section "Submit CPU Smoke Job"

umask 077
mkdir -p "$bundle_dir"
render_job_spec > "$job_spec_file"

az ml job create \
  --file "$job_spec_file" \
  --resource-group "$resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$subscription_id" \
  --output none

if ! az ml job stream \
  --name "$job_name" \
  --resource-group "$resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$subscription_id"; then
  warn "Azure ML log streaming ended before a successful terminal state"
fi

job_deadline=$((SECONDS + 420))
last_job_status=""
while :; do
  az ml job show \
    --name "$job_name" \
    --resource-group "$resource_group" \
    --workspace-name "$workspace_name" \
    --subscription "$subscription_id" \
    --query '{name:name,status:status,compute:compute,environment:environment,creationContext:creation_context}' \
    --output json > "$job_result_file"

  job_status=$(jq -r '.status' "$job_result_file")
  if [[ "$job_status" != "$last_job_status" ]]; then
    info "Azure ML job status: $job_status"
    last_job_status="$job_status"
  fi
  case "$job_status" in
    Completed) break ;;
    Failed|Canceled|NotResponding)
      fatal "CPU smoke job ended with status: $job_status"
      ;;
  esac
  (( SECONDS < job_deadline )) || fatal "Timed out waiting for CPU smoke job: $job_status"
  sleep 5
done

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Job Name" "$job_name"
print_kv "Status" "$job_status"
print_kv "Compute" "$compute_name"
print_kv "Instance Type" "$instance_type"
print_kv "Job Specification" "$job_spec_file"
print_kv "Job Result" "$job_result_file"
info "Azure ML CPU smoke completed"
