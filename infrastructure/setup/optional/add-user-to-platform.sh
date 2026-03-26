#!/usr/bin/env bash
# Add a new user to the platform with all required role assignments
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS] --user <email-or-name>

Add a new user to the platform by assigning all required Azure RBAC roles.

REQUIRED:
    --user EMAIL_OR_NAME     User email (UPN) or display name to add

OPTIONS:
    -h, --help               Show this help message
    -t, --tf-dir DIR         Terraform directory (default: $DEFAULT_TF_DIR)
    --user-id OBJECT_ID      Provide user object ID directly (skip lookup)
    --skip-aks               Skip AKS cluster role assignments
    --skip-keyvault          Skip Key Vault role assignments
    --skip-storage           Skip Storage Account role assignments
    --skip-grafana           Skip Grafana role assignments
    --skip-acr               Skip Container Registry push/pull roles
    --skip-ml                Skip AzureML Data Scientist role
    --skip-contributor       Skip Resource Group Contributor role
    --dry-run                Show commands without executing
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --user "john.doe@contoso.com"
    $(basename "$0") --user "John Doe"
    $(basename "$0") --user "john.doe@contoso.com" --skip-acr --skip-ml
    $(basename "$0") --user-id "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
user_identifier=""
user_object_id=""
skip_aks=false
skip_keyvault=false
skip_storage=false
skip_grafana=false
skip_acr=false
skip_ml=false
skip_contributor=false
dry_run=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    -t|--tf-dir)           tf_dir="$2"; shift 2 ;;
    --user)                user_identifier="$2"; shift 2 ;;
    --user-id)             user_object_id="$2"; shift 2 ;;
    --skip-aks)            skip_aks=true; shift ;;
    --skip-keyvault)       skip_keyvault=true; shift ;;
    --skip-storage)        skip_storage=true; shift ;;
    --skip-grafana)        skip_grafana=true; shift ;;
    --skip-acr)            skip_acr=true; shift ;;
    --skip-ml)             skip_ml=true; shift ;;
    --skip-contributor)    skip_contributor=true; shift ;;
    --dry-run)             dry_run=true; shift ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$user_identifier" || -n "$user_object_id" ]] || { show_help; fatal "Either --user or --user-id is required"; }

require_tools az terraform jq

#------------------------------------------------------------------------------
# Lookup User Object ID
#------------------------------------------------------------------------------

lookup_user_id() {
  local identifier="$1"
  local result=""

  # Check if identifier looks like an email (contains @)
  if [[ "$identifier" == *"@"* ]]; then
    info "Looking up user by email: $identifier" >&2
    result=$(az ad user show --id "$identifier" --query id --output tsv 2>/dev/null) || true
  else
    info "Looking up user by display name: $identifier" >&2
    result=$(az ad user list --display-name "$identifier" --query "[0].id" --output tsv 2>/dev/null) || true
  fi
  echo "$result"
}

#------------------------------------------------------------------------------
# Assign Role with Idempotency Check
#------------------------------------------------------------------------------

assign_role() {
  local role="$1"
  local scope="$2"
  local scope_name="$3"

  if [[ "$dry_run" == "true" ]]; then
    echo "[DRY-RUN] az role assignment create --assignee-object-id \"$user_object_id\" --assignee-principal-type User --role \"$role\" --scope \"$scope\""
    return
  fi

  # Check if assignment already exists
  local existing
  existing=$(az role assignment list --assignee "$user_object_id" --role "$role" --scope "$scope" --query "[0].id" --output tsv 2>/dev/null || true)

  if [[ -n "$existing" ]]; then
    info "Role '$role' already assigned on $scope_name"
  else
    info "Assigning '$role' on $scope_name..."
    az role assignment create \
      --assignee-object-id "$user_object_id" \
      --assignee-principal-type User \
      --role "$role" \
      --scope "$scope" \
      --output none
  fi
}

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
subscription_id=$(tf_get "$tf_output" "subscription_id.value")
[[ -n "$subscription_id" ]] || subscription_id=$(az account show --query id --output tsv)

# Build resource group ID
rg_id="/subscriptions/$subscription_id/resourceGroups/$rg"

# Get resource IDs from terraform outputs
aks_cluster_id=$(tf_get "$tf_output" "aks_cluster.value.id")
keyvault_id=$(tf_get "$tf_output" "key_vault.value.id")
storage_id=$(tf_get "$tf_output" "storage_account.value.id")
grafana_id=$(tf_get "$tf_output" "grafana.value.id")
acr_id=$(tf_get "$tf_output" "container_registry.value.id")
ml_workspace_id=$(tf_get "$tf_output" "azureml_workspace.value.id")

# Resolve user object ID
if [[ -z "$user_object_id" ]]; then
  user_object_id=$(lookup_user_id "$user_identifier")
  [[ -n "$user_object_id" ]] || fatal "Could not find user: $user_identifier"
fi

info "User Object ID: $user_object_id"

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "User" "${user_identifier:-$user_object_id}"
  print_kv "User Object ID" "$user_object_id"
  print_kv "Resource Group" "$rg"
  print_kv "Subscription" "$subscription_id"
  echo
  echo "Role Assignments:"
  [[ "$skip_aks" == "true" ]]      && echo "  AKS Cluster: Skipped"      || echo "  AKS Cluster: Cluster Admin, RBAC Cluster Admin"
  [[ "$skip_keyvault" == "true" ]] && echo "  Key Vault: Skipped"        || echo "  Key Vault: Secrets Officer"
  [[ "$skip_storage" == "true" ]]  && echo "  Storage: Skipped"          || echo "  Storage: Blob Data Contributor"
  [[ "$skip_grafana" == "true" ]]     && echo "  Grafana: Skipped"               || echo "  Grafana: Admin"
  [[ "$skip_acr" == "true" ]]         && echo "  ACR: Skipped"                   || echo "  ACR: AcrPush, AcrPull"
  [[ "$skip_ml" == "true" ]]          && echo "  ML Workspace: Skipped"          || echo "  ML Workspace: Data Scientist"
  [[ "$skip_contributor" == "true" ]] && echo "  Resource Group: Skipped"        || echo "  Resource Group: Contributor"
  exit 0
fi

[[ "$dry_run" == "true" ]] && section "Dry Run Mode - Commands Only"

#------------------------------------------------------------------------------
# AKS Cluster Role Assignments
#------------------------------------------------------------------------------

if [[ "$skip_aks" == "false" ]]; then
  section "AKS Cluster Roles"

  [[ -n "$aks_cluster_id" ]] || fatal "AKS cluster ID not found in terraform outputs"

  assign_role "Azure Kubernetes Service Cluster Admin Role" "$aks_cluster_id" "AKS Cluster"
  assign_role "Azure Kubernetes Service RBAC Cluster Admin" "$aks_cluster_id" "AKS Cluster"
fi

#------------------------------------------------------------------------------
# Key Vault Role Assignments
#------------------------------------------------------------------------------

if [[ "$skip_keyvault" == "false" ]]; then
  section "Key Vault Roles"

  [[ -n "$keyvault_id" ]] || fatal "Key Vault ID not found in terraform outputs"

  assign_role "Key Vault Secrets Officer" "$keyvault_id" "Key Vault"
fi

#------------------------------------------------------------------------------
# Storage Account Role Assignments
#------------------------------------------------------------------------------

if [[ "$skip_storage" == "false" ]]; then
  section "Storage Account Roles"

  [[ -n "$storage_id" ]] || fatal "Storage Account ID not found in terraform outputs"

  assign_role "Storage Blob Data Contributor" "$storage_id" "Storage Account"
fi

#------------------------------------------------------------------------------
# Grafana Role Assignments
#------------------------------------------------------------------------------

if [[ "$skip_grafana" == "false" ]]; then
  section "Grafana Roles"

  [[ -n "$grafana_id" ]] || fatal "Grafana ID not found in terraform outputs"

  assign_role "Grafana Admin" "$grafana_id" "Grafana"
fi

#------------------------------------------------------------------------------
# Container Registry Role Assignments
#------------------------------------------------------------------------------

if [[ "$skip_acr" == "false" ]]; then
  section "Container Registry Roles"

  [[ -n "$acr_id" ]] || fatal "Container Registry ID not found in terraform outputs"

  assign_role "AcrPush" "$acr_id" "Container Registry"
  assign_role "AcrPull" "$acr_id" "Container Registry"
fi

#------------------------------------------------------------------------------
# AzureML Role Assignments
#------------------------------------------------------------------------------

if [[ "$skip_ml" == "false" ]]; then
  section "AzureML Roles"

  [[ -n "$ml_workspace_id" ]] || fatal "ML Workspace ID not found in terraform outputs"

  assign_role "AzureML Data Scientist" "$ml_workspace_id" "ML Workspace"
fi

#------------------------------------------------------------------------------
# Resource Group Contributor
#------------------------------------------------------------------------------

if [[ "$skip_contributor" == "false" ]]; then
  section "Resource Group Roles"

  assign_role "Contributor" "$rg_id" "Resource Group"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Role Assignment Summary"
print_kv "User" "${user_identifier:-$user_object_id}"
print_kv "User Object ID" "$user_object_id"
print_kv "Resource Group" "$rg"
echo

if [[ "$dry_run" == "true" ]]; then
  warn "Dry run mode - no changes were made"
else
  info "Listing role assignments for user..."
  az role assignment list --assignee "$user_object_id" --all \
    --query "[].{Role:roleDefinitionName, Scope:scope}" \
    --output table
fi

echo
info "User onboarding complete"
