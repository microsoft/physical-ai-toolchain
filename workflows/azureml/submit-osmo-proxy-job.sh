#!/usr/bin/env bash
# Submit an AML → OSMO proxy job to Azure ML.
#
# Runs osmo_proxy.py inside AML on AKS, which submits the specified OSMO
# workflow YAML, polls to terminal state, logs Tier 1 metrics to MLflow,
# and registers declared output blob paths as AML data assets.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"

# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit an AML → OSMO proxy job to Azure ML. The proxy runs inside AKS,
submits the specified OSMO workflow YAML to the OSMO REST API, polls to
terminal state, and logs Tier 1 metrics to MLflow.

OPTIONS:
    -h, --help                   Show this help message
    --workflow-yaml PATH         OSMO workflow YAML path relative to repo root
                                 (default: workflows/osmo/smoke-test-proxy-e2e.yaml)
    --config-preview             Print configuration and exit

ENVIRONMENT VARIABLES:
    AZURE_SUBSCRIPTION_ID        Override subscription ID (default: from az account)
    AZURE_RESOURCE_GROUP         Override resource group (default: from Terraform)
    AZUREML_WORKSPACE_NAME       Override AML workspace name (default: from Terraform)

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --workflow-yaml workflows/osmo/my-workflow.yaml
    $(basename "$0") --config-preview
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

workflow_yaml="workflows/osmo/smoke-test-proxy-e2e.yaml"
config_preview=false

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --workflow-yaml)   workflow_yaml="$2"; shift 2 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools az

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
storage_account="$(get_storage_account)"
compute_name="${AZUREML_COMPUTE_NAME:-$(get_compute_target 2>/dev/null || echo "k8s-compute")}"
azure_client_id="$(get_output '.ml_workload_identity.value.client_id' 2>/dev/null || echo "")"

output_url="azure://${storage_account}/proxy-smoke-test/"
set_variables="[{\"name\":\"output_url\",\"value\":\"${output_url}\"}]"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Workflow YAML"    "$workflow_yaml"
  print_kv "AML Workspace"    "${workspace_name:-<not set>}"
  print_kv "Resource Group"   "${resource_group:-<not set>}"
  print_kv "Subscription"     "${subscription_id:-<not set>}"
  print_kv "Storage Account"  "${storage_account:-<not set>}"
  print_kv "Output URL"       "$output_url"
  print_kv "Compute"          "${compute_name:-<not set>}"
  print_kv "Client ID"        "${azure_client_id:-<not set>}"
  exit 0
fi

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required (or configure via az login)"
[[ -n "$resource_group" ]]  || fatal "AZURE_RESOURCE_GROUP required (or deploy Terraform first)"
[[ -n "$workspace_name" ]]  || fatal "AZUREML_WORKSPACE_NAME required (or deploy Terraform first)"

#------------------------------------------------------------------------------
# Submit AML Job
#------------------------------------------------------------------------------

section "Submit AML → OSMO Proxy Job"
print_kv "Workflow YAML"  "$workflow_yaml"
print_kv "AML Workspace"  "$workspace_name"
print_kv "Resource Group" "$resource_group"
print_kv "Compute"        "$compute_name"
print_kv "Output URL"     "$output_url"

job_file="$REPO_ROOT/workflows/azureml/osmo-proxy-job.yaml"
[[ -f "$job_file" ]] || fatal "Job file not found: $job_file"

az_args=(
  az ml job create
  --file "$job_file"
  --workspace-name "$workspace_name"
  --resource-group "$resource_group"
  --subscription "$subscription_id"
  --set "compute=azureml:${compute_name}"
  --set "resources.instance_type=defaultinstancetype"
  --set "environment_variables.WORKFLOW_YAML=${workflow_yaml}"
  --set "environment_variables.AML_SUBSCRIPTION_ID=${subscription_id}"
  --set "environment_variables.AML_RESOURCE_GROUP=${resource_group}"
  --set "environment_variables.AML_WORKSPACE_NAME=${workspace_name}"
  --set "environment_variables.OSMO_SET_VARIABLES=${set_variables}"
)

# shellcheck disable=SC2206
[[ -n "$azure_client_id" ]] && az_args+=(--set "environment_variables.AZURE_CLIENT_ID=${azure_client_id}")

"${az_args[@]}"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Workflow YAML"   "$workflow_yaml"
print_kv "AML Workspace"   "$workspace_name"
print_kv "Resource Group"  "$resource_group"
print_kv "Output URL"      "$output_url"
print_kv "AML Studio" \
  "https://ml.azure.com/experiments?wsid=/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.MachineLearningServices/workspaces/${workspace_name}"
info "Job submitted successfully"
