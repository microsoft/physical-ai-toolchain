#!/usr/bin/env bash
# Uninstall Volcano Scheduler from AKS cluster
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Uninstall Volcano Scheduler from AKS cluster.
Note: Does NOT delete azureml namespace - ML extension uses it.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --delete-namespace      Also delete the azureml namespace
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --delete-namespace
    $(basename "$0") -t /path/to/terraform
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
delete_namespace=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -t|--tf-dir)         tf_dir="$2"; shift 2 ;;
    --delete-namespace)  delete_namespace=true; shift ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform kubectl helm jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  print_kv "Resource Group" "$rg"
  print_kv "Namespace" "$NS_AZUREML"
  print_kv "Delete Namespace" "$delete_namespace"
  exit 0
fi

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Uninstall Volcano Scheduler
#------------------------------------------------------------------------------
section "Uninstall Volcano Scheduler"

if helm status volcano -n "$NS_AZUREML" &>/dev/null; then
  info "Uninstalling Volcano..."
  helm uninstall volcano -n "$NS_AZUREML" --wait --timeout "$TIMEOUT_DEPLOY"
  info "Volcano Scheduler uninstalled"
else
  info "Volcano not found, skipping..."
fi

#------------------------------------------------------------------------------
# Cleanup Namespace
#------------------------------------------------------------------------------

if [[ "$delete_namespace" == "true" ]]; then
  section "Cleanup Namespace"

  if kubectl get namespace "$NS_AZUREML" &>/dev/null; then
    warn "Deleting namespace '$NS_AZUREML'..."
    kubectl delete namespace "$NS_AZUREML" --ignore-not-found --timeout=60s || true
  else
    info "Namespace '$NS_AZUREML' not found, skipping..."
  fi
else
  info "Skipping namespace deletion (use --delete-namespace to remove)"
fi

#------------------------------------------------------------------------------
# Verification
#------------------------------------------------------------------------------
section "Verification"

if helm status volcano -n "$NS_AZUREML" &>/dev/null; then
  warn "Volcano release still exists"
else
  info "Volcano release removed"
fi

if kubectl get namespace "$NS_AZUREML" &>/dev/null; then
  info "Namespace '$NS_AZUREML' preserved (used by AzureML extension)"
else
  info "Namespace '$NS_AZUREML' removed"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Uninstall Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "Namespace" "$NS_AZUREML"
print_kv "Namespace Deleted" "$delete_namespace"
echo
info "To reinstall, run: ./deploy-volcano-scheduler.sh"

info "Volcano Scheduler uninstall complete"
