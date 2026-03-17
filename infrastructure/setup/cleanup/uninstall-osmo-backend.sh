#!/usr/bin/env bash
# Uninstall OSMO Backend Operator and clean up resources
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Uninstall OSMO Backend Operator and clean up Kubernetes resources.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --backend-name NAME     Backend identifier (default: default)
    --skip-osmo-config      Skip removing OSMO configurations
    --skip-k8s-cleanup      Skip cleaning up K8s resources
    --delete-container      Delete the storage container (destructive)
    --container-name NAME   Blob container name (default: osmo)
    --use-local-osmo        Use local osmo-dev CLI instead of production osmo
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --skip-osmo-config
    $(basename "$0") --delete-container
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
backend_name="default"
skip_osmo_config=false
skip_k8s_cleanup=false
delete_container=false
container_name="osmo"
use_local_osmo=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -t|--tf-dir)          tf_dir="$2"; shift 2 ;;
    --backend-name)       backend_name="$2"; shift 2 ;;
    --skip-osmo-config)   skip_osmo_config=true; shift ;;
    --skip-k8s-cleanup)   skip_k8s_cleanup=true; shift ;;
    --delete-container)   delete_container=true; shift ;;
    --container-name)     container_name="$2"; shift 2 ;;
    --use-local-osmo)     use_local_osmo=true; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools az terraform kubectl helm jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
storage_name=$(tf_get "$tf_output" "storage_account.value.name")

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  print_kv "Resource Group" "$rg"
  print_kv "Backend Name" "$backend_name"
  print_kv "Storage Account" "${storage_name:-<not configured>}"
  print_kv "Container" "$container_name"
  print_kv "Delete Container" "$delete_container"
  exit 0
fi

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Remove OSMO Configurations
#------------------------------------------------------------------------------

if [[ "$skip_osmo_config" == "false" ]]; then
  section "Remove OSMO Configurations"

  if command -v osmo &>/dev/null; then
    for config_type in "POOL $backend_name" "BACKEND $backend_name" "WORKFLOW" "POD_TEMPLATE"; do
      # shellcheck disable=SC2086
      if osmo config get $config_type &>/dev/null 2>&1; then
        info "Removing OSMO config: $config_type..."
        # shellcheck disable=SC2086
        osmo config delete $config_type 2>/dev/null || true
      else
        info "OSMO config '$config_type' not found, skipping..."
      fi
    done
  else
    warn "osmo CLI not found, skipping configuration cleanup"
  fi
else
  info "Skipping OSMO config removal (--skip-osmo-config)"
fi

#------------------------------------------------------------------------------
# Uninstall Helm Release
#------------------------------------------------------------------------------
section "Uninstall Backend Operator"

if helm status osmo-operator -n "$NS_OSMO_OPERATOR" &>/dev/null; then
  info "Uninstalling osmo-operator Helm release..."
  helm uninstall osmo-operator -n "$NS_OSMO_OPERATOR" --wait --timeout "$TIMEOUT_DEPLOY"
else
  info "Helm release 'osmo-operator' not found, skipping..."
fi

#------------------------------------------------------------------------------
# Cleanup Kubernetes Resources
#------------------------------------------------------------------------------

if [[ "$skip_k8s_cleanup" == "false" ]]; then
  section "Cleanup Kubernetes Resources"

  if kubectl get secret "osmo-operator-token" -n "$NS_OSMO_OPERATOR" &>/dev/null; then
    info "Deleting secret 'osmo-operator-token'..."
    kubectl delete secret "osmo-operator-token" -n "$NS_OSMO_OPERATOR" --ignore-not-found
  fi

  # Delete workflow ServiceAccount
  if kubectl get serviceaccount "$WORKFLOW_SERVICE_ACCOUNT" -n "$NS_OSMO_WORKFLOWS" &>/dev/null; then
    info "Deleting workflow ServiceAccount..."
    kubectl delete serviceaccount "$WORKFLOW_SERVICE_ACCOUNT" -n "$NS_OSMO_WORKFLOWS" --ignore-not-found
  fi

  # Delete namespaces
  for ns in "$NS_OSMO_WORKFLOWS" "$NS_OSMO_OPERATOR"; do
    if kubectl get namespace "$ns" &>/dev/null; then
      info "Deleting namespace '$ns'..."
      kubectl delete namespace "$ns" --ignore-not-found --timeout=60s || true
    fi
  done
else
  info "Skipping K8s cleanup (--skip-k8s-cleanup)"
fi

#------------------------------------------------------------------------------
# Delete Storage Container
#------------------------------------------------------------------------------

if [[ "$delete_container" == "true" ]]; then
  section "Delete Storage Container"

  if [[ -z "$storage_name" ]]; then
    warn "Storage account not found in terraform outputs, skipping..."
  elif az storage container show --account-name "$storage_name" --name "$container_name" --auth-mode login &>/dev/null; then
    warn "Deleting container '$container_name' (this will delete all workflow data)..."
    az storage container delete --account-name "$storage_name" --name "$container_name" --auth-mode login
  else
    info "Container '$container_name' not found, skipping..."
  fi
else
  info "Skipping container deletion (use --delete-container to remove)"
fi

#------------------------------------------------------------------------------
# Verification
#------------------------------------------------------------------------------
section "Verification"

if helm status osmo-operator -n "$NS_OSMO_OPERATOR" &>/dev/null; then
  warn "Helm release 'osmo-operator' still exists"
else
  info "Helm release removed"
fi

if kubectl get namespace "$NS_OSMO_OPERATOR" &>/dev/null; then
  warn "$NS_OSMO_OPERATOR namespace still exists (may be terminating)"
else
  info "$NS_OSMO_OPERATOR namespace removed"
fi

if kubectl get namespace "$NS_OSMO_WORKFLOWS" &>/dev/null; then
  warn "$NS_OSMO_WORKFLOWS namespace still exists (may be terminating)"
else
  info "$NS_OSMO_WORKFLOWS namespace removed"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Uninstall Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "Backend Name" "$backend_name"
print_kv "Storage Container" "$([[ $delete_container == true ]] && echo 'deleted' || echo 'preserved')"
echo
info "To reinstall, run: ../04-deploy-osmo-backend.sh"

info "OSMO backend uninstall complete"
