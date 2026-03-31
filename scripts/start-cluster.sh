#!/usr/bin/env bash
# Start Azure resources (AKS cluster and PostgreSQL) via Automation Runbook
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

# shellcheck source=../deploy/002-setup/lib/common.sh
source "$REPO_ROOT/deploy/002-setup/lib/common.sh"

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
Usage: start-cluster.sh [OPTIONS]

Start Azure infrastructure (AKS cluster and PostgreSQL) by triggering the
Start-AzureResources runbook in Azure Automation.

OPTIONS:
    -g, --resource-group NAME       Resource group (default: from env/terraform)
    -c, --cluster-name NAME         AKS cluster name (default: from env/terraform)
    -p, --postgres-name NAME        PostgreSQL server name (default: from env/terraform)
    -a, --automation-account NAME   Automation account name (default: derived from naming convention)
    -s, --subscription-id ID        Azure subscription ID
        --no-wait                   Start runbook and exit without waiting for completion
        --config-preview            Print configuration and exit
    -h, --help                      Show this help message

VALUES RESOLVED: CLI > Environment variables > Naming convention

EXAMPLES:
    # Start with auto-discovered values
    start-cluster.sh

    # Start specific cluster
    start-cluster.sh -g rg-osmorbt3-dev-001 -c aks-osmorbt3-dev-001

    # Preview configuration only
    start-cluster.sh --config-preview
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
resource_group="${AZURE_RESOURCE_GROUP:-rg-osmorbt3-dev-001}"
cluster_name="${AKS_CLUSTER_NAME:-aks-osmorbt3-dev-001}"
postgres_name="${POSTGRES_SERVER_NAME:-psql-osmorbt3-dev-001}"
automation_account="${AUTOMATION_ACCOUNT_NAME:-aa-osmorbt3-dev-001}"
no_wait=false
config_preview=false

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                show_help; exit 0 ;;
    -g|--resource-group)      resource_group="$2"; shift 2 ;;
    -c|--cluster-name)        cluster_name="$2"; shift 2 ;;
    -p|--postgres-name)       postgres_name="$2"; shift 2 ;;
    -a|--automation-account)  automation_account="$2"; shift 2 ;;
    -s|--subscription-id)     subscription_id="$2"; shift 2 ;;
    --no-wait)                no_wait=true; shift ;;
    --config-preview)         config_preview=true; shift ;;
    *)                        fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

[[ -z "$resource_group" ]] && fatal "--resource-group is required"
[[ -z "$cluster_name" ]] && fatal "--cluster-name is required"
[[ -z "$automation_account" ]] && fatal "--automation-account is required"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription"       "${subscription_id:-<will resolve from az account>}"
  print_kv "Resource Group"     "$resource_group"
  print_kv "AKS Cluster"        "$cluster_name"
  print_kv "PostgreSQL Server"  "$postgres_name"
  print_kv "Automation Account" "$automation_account"
  print_kv "Runbook"            "Start-AzureResources"
  print_kv "Wait for result"    "$([[ "$no_wait" == "true" ]] && echo "No" || echo "Yes")"
  exit 0
fi

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools az jq
require_az_extension "automation"

if [[ -z "$subscription_id" ]]; then
  subscription_id=$(az account show --query id -o tsv 2>/dev/null) || fatal "Azure CLI not authenticated. Run 'az login' first."
fi

#------------------------------------------------------------------------------
# Start Resources
#------------------------------------------------------------------------------

section "Starting Azure Resources"
print_kv "Resource Group"     "$resource_group"
print_kv "AKS Cluster"        "$cluster_name"
print_kv "PostgreSQL Server"  "$postgres_name"
print_kv "Automation Account" "$automation_account"

info "Triggering Start-AzureResources runbook..."

job_output=$(az automation runbook start \
  --resource-group "$resource_group" \
  --automation-account-name "$automation_account" \
  --name "Start-AzureResources" \
  --parameters \
    ResourceGroupName="$resource_group" \
    AksClusterName="$cluster_name" \
    PostgresServerName="$postgres_name" \
  --subscription "$subscription_id" \
  -o json) || fatal "Failed to start runbook: $job_output"

job_id=$(echo "$job_output" | jq -r '.jobId // empty')
[[ -z "$job_id" ]] && fatal "No job ID returned from runbook start"

info "Runbook job started: $job_id"

if [[ "$no_wait" == "true" ]]; then
  section "Deployment Summary"
  print_kv "Job ID"   "$job_id"
  print_kv "Status"   "Started (not waiting)"
  info "Check status: az automation job show --resource-group $resource_group --automation-account-name $automation_account --job-name $job_id"
  exit 0
fi

#------------------------------------------------------------------------------
# Wait for Completion
#------------------------------------------------------------------------------

info "Waiting for runbook to complete (this may take several minutes)..."

max_wait=600
poll_interval=15
elapsed=0

while [[ $elapsed -lt $max_wait ]]; do
  status=$(az automation job show \
    --resource-group "$resource_group" \
    --automation-account-name "$automation_account" \
    --job-name "$job_id" \
    --subscription "$subscription_id" \
    --query "status" -o tsv 2>/dev/null) || true

  case "$status" in
    Completed)
      info "Runbook completed successfully."
      break
      ;;
    Failed)
      error "Runbook failed. Check logs in Azure portal."
      az automation job show \
        --resource-group "$resource_group" \
        --automation-account-name "$automation_account" \
        --job-name "$job_id" \
        --subscription "$subscription_id" \
        -o table 2>/dev/null || true
      exit 1
      ;;
    Stopped|Suspended)
      fatal "Runbook stopped unexpectedly with status: $status"
      ;;
    *)
      info "Status: ${status:-unknown} (${elapsed}s elapsed)"
      sleep "$poll_interval"
      elapsed=$((elapsed + poll_interval))
      ;;
  esac
done

if [[ $elapsed -ge $max_wait ]]; then
  warn "Timed out after ${max_wait}s. Job may still be running."
  warn "Check status: az automation job show --resource-group $resource_group --automation-account-name $automation_account --job-name $job_id"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Job ID"             "$job_id"
print_kv "Status"             "$status"
print_kv "Resource Group"     "$resource_group"
print_kv "AKS Cluster"        "$cluster_name"
print_kv "PostgreSQL Server"  "$postgres_name"
info "Cluster resources started"
