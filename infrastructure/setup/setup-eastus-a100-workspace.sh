#!/usr/bin/env bash
# Stand up an Azure ML workspace + A100 managed compute cluster in eastus for
# cross-region OpenVLA-OFT fine-tuning.
#
# Why eastus: the project's primary workspace (mlw-hex-osmo-hack-001) is in
# westus3 with only 96 vCPU A100 quota. eastus has 400 vCPU of A100 quota
# (NCADS_A100_v4), giving room to scale OFT across multiple jobs concurrently.
#
# Resources created (all idempotent):
#   - Resource group ${RESOURCE_GROUP} (default rg-hex-train-eus-002)
#   - Storage account (workspace default datastore)
#   - Key Vault (workspace secrets store)
#   - Application Insights (workspace logging)
#   - AzureML workspace ${WORKSPACE_NAME}
#   - Managed compute cluster ${COMPUTE_NAME} with VM size ${VM_SIZE}
#     (autoscale 0 -> ${MAX_NODES}, idle scale-down 30 min)
#
# After this completes, submit OFT jobs targeting the new workspace:
#   training/il/scripts/submit-azureml-openvla-oft-training.sh \
#     --profile prod-a100 \
#     --resource-group ${RESOURCE_GROUP} \
#     --workspace-name ${WORKSPACE_NAME} \
#     --compute ${COMPUTE_NAME} \
#     --instance-type "" \  # managed compute does not use K8s InstanceType CRDs
#     --dataset-asset schaeffler-sim-avc1-second:1
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat <<'EOF'
Usage: setup-eastus-a100-workspace.sh [OPTIONS]

Stand up an AzureML workspace + A100 managed compute cluster in eastus.

OPTIONS:
    --subscription-id ID       Azure subscription (default: $AZURE_SUBSCRIPTION_ID or current)
    --resource-group NAME      Resource group (default: rg-hex-train-eus-002)
    --location LOCATION        Region (default: eastus)
    --workspace-name NAME      AzureML workspace (default: mlw-hex-train-eus-002)
    --compute-name NAME        Compute cluster name (default: a100-cluster)
    --vm-size SIZE             VM size (default: Standard_NC24ads_A100_v4 = 1x A100 80GB)
    --max-nodes N              Cluster max nodes (default: 2)
    --idle-min N               Idle scale-down minutes (default: 30)
    --tier basic|standard      Workspace tier (default: basic)
    --skip-deps                Skip storage/keyvault/appinsights creation
    --skip-compute             Create workspace only
    --config-preview           Print config and exit
    -h, --help
EOF
}

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2>/dev/null || true)}"
resource_group="rg-hex-train-eus-002"
location="eastus"
workspace_name="mlw-hex-train-eus-002"
compute_name="a100-cluster"
vm_size="Standard_NC24ads_A100_v4"
max_nodes=2
idle_min=30
tier="basic"
skip_deps=false
skip_compute=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    --subscription-id)    subscription_id="$2"; shift 2 ;;
    --resource-group)     resource_group="$2"; shift 2 ;;
    --location)           location="$2"; shift 2 ;;
    --workspace-name)     workspace_name="$2"; shift 2 ;;
    --compute-name)       compute_name="$2"; shift 2 ;;
    --vm-size)            vm_size="$2"; shift 2 ;;
    --max-nodes)          max_nodes="$2"; shift 2 ;;
    --idle-min)           idle_min="$2"; shift 2 ;;
    --tier)               tier="$2"; shift 2 ;;
    --skip-deps)          skip_deps=true; shift ;;
    --skip-compute)       skip_compute=true; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required (run az login first)"
require_tools az

# Derive co-located resource names (deterministic, length-capped where Azure cares).
# Storage account: 3-24 lowercase alphanumeric; strip prefix and collapse hyphens.
suffix=$(echo "${workspace_name#mlw-}" | tr -d '-' | cut -c1-18)
storage_account="st${suffix}"
storage_account="${storage_account:0:24}"
keyvault_name="kv${suffix}"
keyvault_name="${keyvault_name:0:24}"
appinsights_name="appi-${workspace_name#mlw-}"

section "Configuration"
print_kv "Subscription"   "$subscription_id"
print_kv "Resource group" "$resource_group ($location)"
print_kv "Workspace"      "$workspace_name (tier=$tier)"
print_kv "Storage"        "$storage_account"
print_kv "Key Vault"      "$keyvault_name"
print_kv "App Insights"   "$appinsights_name"
print_kv "Compute"        "$compute_name ($vm_size, max=$max_nodes, idle=${idle_min}m)"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi

az account set --subscription "$subscription_id"

# ---- Resource group ----
section "Resource group"
if az group show -n "$resource_group" --subscription "$subscription_id" >/dev/null 2>&1; then
  info "Resource group $resource_group already exists"
else
  az group create -n "$resource_group" -l "$location" --subscription "$subscription_id" \
    --tags purpose=openvla-oft-training >/dev/null
  info "Created $resource_group"
fi

# ---- Workspace dependencies ----
if [[ "$skip_deps" == "false" ]]; then
  section "Workspace dependencies"

  if ! az storage account show -n "$storage_account" -g "$resource_group" >/dev/null 2>&1; then
    info "Creating storage account $storage_account..."
    az storage account create \
      --name "$storage_account" -g "$resource_group" -l "$location" \
      --sku Standard_LRS --kind StorageV2 --hns false \
      --allow-blob-public-access false \
      --min-tls-version TLS1_2 >/dev/null
  else
    info "Storage $storage_account exists"
  fi

  if ! az keyvault show -n "$keyvault_name" -g "$resource_group" >/dev/null 2>&1; then
    info "Creating Key Vault $keyvault_name..."
    az keyvault create \
      --name "$keyvault_name" -g "$resource_group" -l "$location" \
      --enable-rbac-authorization true \
      --enable-purge-protection true >/dev/null
  else
    info "Key Vault $keyvault_name exists"
  fi

  if ! az monitor app-insights component show --app "$appinsights_name" -g "$resource_group" >/dev/null 2>&1; then
    info "Creating Application Insights $appinsights_name..."
    az extension add --name application-insights --upgrade --yes >/dev/null 2>&1 || true
    az monitor app-insights component create \
      --app "$appinsights_name" -g "$resource_group" -l "$location" \
      --kind web --application-type web >/dev/null
  else
    info "App Insights $appinsights_name exists"
  fi
fi

# ---- AzureML workspace ----
section "AzureML workspace"
if az ml workspace show -n "$workspace_name" -g "$resource_group" >/dev/null 2>&1; then
  info "Workspace $workspace_name already exists"
else
  storage_id=$(az storage account show -n "$storage_account" -g "$resource_group" --query id -o tsv)
  keyvault_id=$(az keyvault show -n "$keyvault_name" -g "$resource_group" --query id -o tsv)
  appinsights_id=$(az monitor app-insights component show --app "$appinsights_name" -g "$resource_group" --query id -o tsv)

  ws_yaml=$(mktemp)
  cat >"$ws_yaml" <<EOF
\$schema: https://azuremlschemas.azureedge.net/latest/workspace.schema.json
name: $workspace_name
location: $location
description: OpenVLA-OFT training (eastus A100 quota)
tags:
  purpose: openvla-oft-training
storage_account: $storage_id
key_vault: $keyvault_id
application_insights: $appinsights_id
public_network_access: Enabled
EOF
  az ml workspace create --file "$ws_yaml" -g "$resource_group" >/dev/null
  rm -f "$ws_yaml"
  info "Created workspace $workspace_name"
fi

# ---- Compute cluster ----
if [[ "$skip_compute" == "false" ]]; then
  section "Managed compute cluster"
  if az ml compute show --name "$compute_name" -w "$workspace_name" -g "$resource_group" >/dev/null 2>&1; then
    info "Compute $compute_name already exists; updating scale settings"
    az ml compute update \
      --name "$compute_name" -w "$workspace_name" -g "$resource_group" \
      --max-instances "$max_nodes" \
      --idle-time-before-scale-down $((idle_min * 60)) >/dev/null
  else
    info "Creating compute cluster $compute_name..."
    compute_yaml=$(mktemp)
    cat >"$compute_yaml" <<EOF
\$schema: https://azuremlschemas.azureedge.net/latest/amlCompute.schema.json
name: $compute_name
type: amlcompute
size: $vm_size
min_instances: 0
max_instances: $max_nodes
idle_time_before_scale_down: $((idle_min * 60))
tier: Dedicated
EOF
    az ml compute create --file "$compute_yaml" -w "$workspace_name" -g "$resource_group" >/dev/null
    rm -f "$compute_yaml"
    info "Created compute $compute_name"
  fi
fi

section "Done"
print_kv "Workspace URI"  "azureml://subscriptions/$subscription_id/resourceGroups/$resource_group/workspaces/$workspace_name"
print_kv "Submit example" "training/il/scripts/submit-azureml-openvla-oft-training.sh --profile prod-a100 --resource-group $resource_group --workspace-name $workspace_name --compute $compute_name --dataset-asset <NAME:VERSION>"
print_kv "Quota check"    "az vm list-usage --location $location --query \"[?name.value=='standardNCADSA100v4Family']\" -o table"
