#!/usr/bin/env bash
# Configure isolated AKS connectivity before an environment bundle is available.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Create an isolated, target-validated AKS kubeconfig before an environment bundle is available.

OPTIONS:
    -h, --help                  Show this help message
    --subscription-id ID        Expected Azure subscription ID (required)
    --tenant-id ID              Expected Microsoft Entra tenant ID (required)
    --resource-group NAME       AKS resource group (required)
    --cluster-name NAME         AKS cluster name (required)
    --aks-resource-id ID        Expected complete AKS resource ID (required)
    --kubeconfig PATH           External isolated kubeconfig output (required)
    --context NAME              Explicit context (default: cluster name)
    --config-preview            Print configuration and exit

EXAMPLES:
    $(basename "$0") --subscription-id <id> --tenant-id <id> \
      --resource-group <resource-group> --cluster-name <cluster> \
      --aks-resource-id /subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.ContainerService/managedClusters/<cluster> \
      --kubeconfig ~/.kube/physical-ai-toolchain/<cluster>.yaml --config-preview
EOF
}

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
tenant_id="${AZURE_TENANT_ID:-}"
resource_group="${AZURE_RESOURCE_GROUP:-}"
cluster_name="${AKS_CLUSTER_NAME:-}"
aks_resource_id="${AKS_RESOURCE_ID:-}"
kubeconfig=""
context=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    --subscription-id)     subscription_id="$2"; shift 2 ;;
    --tenant-id)           tenant_id="$2"; shift 2 ;;
    --resource-group)      resource_group="$2"; shift 2 ;;
    --cluster-name)        cluster_name="$2"; shift 2 ;;
    --aks-resource-id)     aks_resource_id="$2"; shift 2 ;;
    --kubeconfig)          kubeconfig="$2"; shift 2 ;;
    --context)             context="$2"; shift 2 ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq kubectl kubelogin

[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ -n "$aks_resource_id" ]] || fatal "--aks-resource-id is required"
[[ -n "$kubeconfig" ]] || fatal "--kubeconfig is required"
context="${context:-$cluster_name}"
require_external_runtime_path "$kubeconfig"

expected_resource_id="/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.ContainerService/managedClusters/${cluster_name}"
[[ "$(printf '%s' "$aks_resource_id" | tr '[:upper:]' '[:lower:]')" == \
  "$(printf '%s' "$expected_resource_id" | tr '[:upper:]' '[:lower:]')" ]] || \
  fatal "--aks-resource-id does not match the supplied subscription, resource group, and cluster"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Tenant" "$tenant_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "AKS Cluster" "$cluster_name"
  print_kv "AKS Resource ID" "$aks_resource_id"
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Context" "$context"
  exit 0
fi

az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login --use-device-code'"
account_json=$(az account show -o json)
[[ "$(jq -r '.id' <<< "$account_json")" == "$subscription_id" ]] || \
  fatal "Active Azure subscription does not match --subscription-id"
[[ "$(jq -r '.tenantId' <<< "$account_json")" == "$tenant_id" ]] || \
  fatal "Active Microsoft Entra tenant does not match --tenant-id"
az group show --subscription "$subscription_id" --name "$resource_group" >/dev/null || \
  fatal "Cannot access Azure resource group $resource_group"
live_resource_id=$(az aks show --subscription "$subscription_id" --resource-group "$resource_group" \
  --name "$cluster_name" --query id -o tsv)
[[ "$(printf '%s' "$live_resource_id" | tr '[:upper:]' '[:lower:]')" == \
  "$(printf '%s' "$aks_resource_id" | tr '[:upper:]' '[:lower:]')" ]] || \
  fatal "Live AKS resource does not match --aks-resource-id"

verify_existing_aks_kubeconfig "$kubeconfig" "$context" "$aks_resource_id"
connect_aks "$resource_group" "$cluster_name" "$kubeconfig" "$context"

section "Deployment Summary"
print_kv "Subscription" "$subscription_id"
print_kv "Resource Group" "$resource_group"
print_kv "AKS Cluster" "$cluster_name"
print_kv "Kubeconfig" "$kubeconfig"
print_kv "Context" "$context"
info "Isolated AKS connectivity configured"
