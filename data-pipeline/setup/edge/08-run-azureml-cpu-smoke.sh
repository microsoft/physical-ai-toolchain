#!/usr/bin/env bash
# Verify managed output-to-input staging on Azure ML attached Kubernetes compute.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

GENERATED_ROOT="$REPO_ROOT/infrastructure/setup/generated"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit a bounded CPU-only writer-to-reader pipeline to an existing Azure ML
workspace and attached Kubernetes compute target.

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
      "$schema": "https://azuremlschemas.azureedge.net/latest/pipelineJob.schema.json",
      "type": "pipeline",
      "name": $job_name,
      "display_name": "Physical AI managed data smoke",
      "description": "Verify that a managed reader can download a managed writer output.",
      "experiment_name": "physical-ai-managed-data-smoke",
      "settings": {
        "default_compute": $compute,
        "continue_on_step_failure": false
      },
      "jobs": {
        "writer": {
          "type": "command",
          "display_name": "Managed data smoke writer",
          "environment": $environment,
          "identity": {"type": "managed"},
          "resources": {
            "instance_count": 1,
            "instance_type": $instance_type
          },
          "inputs": {
            "expected_token": "arc-managed-data-smoke-v1"
          },
          "outputs": {
            "smoke": {
              "type": "uri_folder",
              "mode": "upload"
            }
          },
          "limits": {"timeout": 300},
          "command": "set -eu; mkdir -p \"${{outputs.smoke}}\"; printf \"%s\\n\" \"${{inputs.expected_token}}\" > \"${{outputs.smoke}}/result.txt\""
        },
        "reader": {
          "type": "command",
          "display_name": "Managed data smoke reader",
          "environment": $environment,
          "identity": {"type": "managed"},
          "resources": {
            "instance_count": 1,
            "instance_type": $instance_type
          },
          "inputs": {
            "expected_token": "arc-managed-data-smoke-v1",
            "smoke": {
              "type": "uri_folder",
              "path": "${{parent.jobs.writer.outputs.smoke}}",
              "mode": "download"
            }
          },
          "outputs": {
            "verification": {
              "type": "uri_folder",
              "mode": "upload"
            }
          },
          "limits": {"timeout": 300},
          "command": "set -eu; actual=$(cat \"${{inputs.smoke}}/result.txt\"); test \"$actual\" = \"${{inputs.expected_token}}\"; mkdir -p \"${{outputs.verification}}\"; printf \"verified\\n\" > \"${{outputs.verification}}/result.txt\""
        }
      }
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
    .type == "pipeline" and
    .settings.continue_on_step_failure == false and
    .jobs.writer.identity.type == "managed" and
    .jobs.reader.identity.type == "managed" and
    .jobs.writer.resources.instance_count == 1 and
    .jobs.reader.resources.instance_count == 1 and
    .jobs.writer.outputs.smoke.mode == "upload" and
    .jobs.reader.inputs.smoke.mode == "download" and
    .jobs.reader.inputs.smoke.path == "${{parent.jobs.writer.outputs.smoke}}" and
    (.jobs.reader.command | contains("result.txt"))
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
# Render and Submit Managed Data Smoke
#------------------------------------------------------------------------------
section "Submit Managed Data Smoke"

umask 077
mkdir -p "$bundle_dir"
render_job_spec > "$job_spec_file"

az ml job create \
  --file "$job_spec_file" \
  --resource-group "$resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$subscription_id" \
  --output none

job_deadline=$((SECONDS + 900))
last_job_status=""
while :; do
  parent_json=$(az ml job show \
    --name "$job_name" \
    --resource-group "$resource_group" \
    --workspace-name "$workspace_name" \
    --subscription "$subscription_id" \
    --query '{name:name,status:status,compute:compute,environment:environment,creationContext:creation_context}' \
    --output json)
  children_json=$(az ml job list \
    --parent-job-name "$job_name" \
    --resource-group "$resource_group" \
    --workspace-name "$workspace_name" \
    --subscription "$subscription_id" \
    --output json)
  jq -n \
    --argjson parent "$parent_json" \
    --argjson children "$children_json" \
    '{
      parent: $parent,
      children: [$children[] | {
        name: .name,
        displayName: (.display_name // .displayName),
        status: .status
      }]
    }' > "$job_result_file"

  job_status=$(jq -r '.parent.status' "$job_result_file")
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

completed_writer_count=$(jq '[.children[] | select(.displayName == "writer" and .status == "Completed")] | length' "$job_result_file")
completed_reader_count=$(jq '[.children[] | select(.displayName == "reader" and .status == "Completed")] | length' "$job_result_file")
(( completed_writer_count == 1 )) || fatal "Managed data smoke writer did not complete successfully"
(( completed_reader_count == 1 )) || fatal "Managed data smoke reader did not complete successfully"

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
info "Azure ML managed writer-to-reader smoke completed"
