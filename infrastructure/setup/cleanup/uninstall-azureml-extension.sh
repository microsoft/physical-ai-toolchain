#!/usr/bin/env bash
# Uninstall AzureML extension from AKS cluster and clean up resources
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Uninstall Azure Machine Learning extension from AKS cluster,
detach compute target, and clean up federated identity credentials.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --extension-name NAME   Extension name (default: azureml-<cluster>)
    --compute-name NAME     Compute target name (default: k8s-<suffix>)
    --skip-compute-detach   Skip detaching compute target
    --skip-fic-delete       Skip deleting federated identity credentials
    --skip-k8s-cleanup      Skip cleaning up K8s resources
    --force                 Force deletion of extension
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --force
    $(basename "$0") --skip-k8s-cleanup
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
extension_name=""
compute_name=""
skip_compute_detach=false
skip_fic_delete=false
skip_k8s_cleanup=false
force_delete=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -t|--tf-dir)          tf_dir="$2"; shift 2 ;;
    --extension-name)     extension_name="$2"; shift 2 ;;
    --compute-name)       compute_name="$2"; shift 2 ;;
    --skip-compute-detach) skip_compute_detach=true; shift ;;
    --skip-fic-delete)    skip_fic_delete=true; shift ;;
    --skip-k8s-cleanup)   skip_k8s_cleanup=true; shift ;;
    --force)              force_delete=true; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform kubectl jq
require_az_extension k8s-extension
require_az_extension ml

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
ml_workspace=$(tf_get "$tf_output" "azureml_workspace.value.name")
ml_identity_id=$(tf_get "$tf_output" "ml_workload_identity.value.id")

# Set defaults based on cluster name
[[ -z "$extension_name" ]] && extension_name="azureml-$cluster"
[[ -z "$compute_name" ]] && compute_name="k8s-${cluster#aks-}"
[[ -n "$ml_identity_id" ]] && ml_identity_name="${ml_identity_id##*/}"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  print_kv "Resource Group" "$rg"
  print_kv "Extension Name" "$extension_name"
  print_kv "Compute Name" "$compute_name"
  print_kv "ML Workspace" "${ml_workspace:-<not configured>}"
  print_kv "ML Identity" "${ml_identity_name:-<not configured>}"
  print_kv "Force Delete" "$force_delete"
  exit 0
fi

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Detach Compute Target
#------------------------------------------------------------------------------

if [[ "$skip_compute_detach" == "false" ]]; then
  section "Detach Compute Target"

  if [[ -z "$ml_workspace" ]]; then
    info "ML workspace not found in terraform outputs, skipping..."
  elif az ml compute show --name "$compute_name" -g "$rg" -w "$ml_workspace" &>/dev/null; then
    info "Detaching compute target '$compute_name'..."
    az ml compute detach --name "$compute_name" -g "$rg" -w "$ml_workspace" --yes
  else
    info "Compute target '$compute_name' not found, skipping..."
  fi
else
  info "Skipping compute detach (--skip-compute-detach)"
fi

#------------------------------------------------------------------------------
# Delete Federated Identity Credentials
#------------------------------------------------------------------------------

if [[ "$skip_fic_delete" == "false" ]]; then
  section "Delete Federated Identity Credentials"

  if [[ -z "$ml_identity_id" ]]; then
    info "ML identity not found in terraform outputs, skipping..."
  else
    for fic_name in "aml-default-fic" "aml-training-fic"; do
      if az identity federated-credential show --identity-name "$ml_identity_name" \
          --resource-group "$rg" --name "$fic_name" &>/dev/null; then
        info "Deleting federated credential '$fic_name'..."
        az identity federated-credential delete \
          --identity-name "$ml_identity_name" \
          --resource-group "$rg" \
          --name "$fic_name" \
          --yes
      else
        info "Federated credential '$fic_name' not found, skipping..."
      fi
    done
  fi
else
  info "Skipping FIC deletion (--skip-fic-delete)"
fi

#------------------------------------------------------------------------------
# Delete AzureML Extension
#------------------------------------------------------------------------------
section "Delete AzureML Extension"

if az k8s-extension show --name "$extension_name" --cluster-type managedClusters \
    --cluster-name "$cluster" --resource-group "$rg" &>/dev/null; then
  info "Deleting AzureML extension '$extension_name'..."
  delete_args=(--name "$extension_name" --cluster-type managedClusters --cluster-name "$cluster" --resource-group "$rg" --yes)
  [[ "$force_delete" == "true" ]] && delete_args+=(--force)
  az k8s-extension delete "${delete_args[@]}"
  info "Extension deletion initiated"
else
  info "Extension '$extension_name' not found, skipping..."
fi

#------------------------------------------------------------------------------
# Cleanup Kubernetes Resources
#------------------------------------------------------------------------------

if [[ "$skip_k8s_cleanup" == "false" ]]; then
  section "Cleanup Kubernetes Resources"

  info "Waiting for extension deletion to propagate..."
  sleep 30

  if kubectl get crd instancetypes.amlarc.azureml.com &>/dev/null; then
    info "Cleaning up InstanceType resources..."
    kubectl delete instancetype --all --ignore-not-found 2>/dev/null || true
    kubectl delete crd instancetypes.amlarc.azureml.com --ignore-not-found
  fi

  if kubectl get namespace azureml &>/dev/null; then
    info "Cleaning up azureml namespace..."
    kubectl delete namespace azureml --ignore-not-found --timeout=60s || true
  fi
else
  info "Skipping K8s cleanup (--skip-k8s-cleanup)"
fi

#------------------------------------------------------------------------------
# Verification
#------------------------------------------------------------------------------
section "Verification"

remaining_extension=$(az k8s-extension show --name "$extension_name" --cluster-type managedClusters \
    --cluster-name "$cluster" --resource-group "$rg" --query "name" -o tsv 2>/dev/null || true)

if [[ -n "$remaining_extension" ]]; then
  warn "Extension '$extension_name' still exists (may be deleting)"
else
  info "Extension removed"
fi

if kubectl get namespace azureml &>/dev/null; then
  warn "azureml namespace still exists (may be terminating)"
else
  info "azureml namespace removed"
fi

if kubectl get crd instancetypes.amlarc.azureml.com &>/dev/null; then
  warn "InstanceType CRD still exists"
else
  info "InstanceType CRD removed"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Uninstall Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "Extension" "$extension_name"
print_kv "Compute" "$compute_name"
echo
info "To reinstall, run: ../02-deploy-azureml-extension.sh"

info "AzureML extension uninstall complete"
