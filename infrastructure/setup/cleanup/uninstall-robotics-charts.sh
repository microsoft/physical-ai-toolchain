#!/usr/bin/env bash
# Uninstall NVIDIA GPU Operator and KAI Scheduler from AKS cluster
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

MANIFESTS_DIR="$SCRIPT_DIR/../manifests"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Uninstall NVIDIA GPU Operator and KAI Scheduler from an AKS cluster.

OPTIONS:
    -h, --help               Show this help message
    -t, --tf-dir DIR         Terraform directory (default: $DEFAULT_TF_DIR)
    --skip-gpu-operator      Skip GPU Operator uninstallation
    --skip-kai-scheduler     Skip KAI Scheduler uninstallation
    --delete-namespaces      Also delete the gpu-operator and kai-scheduler namespaces
    --delete-crds            Also delete GPU Operator CRDs (destructive)
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --skip-kai-scheduler
    $(basename "$0") --delete-namespaces --delete-crds
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
skip_gpu=false
skip_kai=false
delete_namespaces=false
delete_crds=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -t|--tf-dir)          tf_dir="$2"; shift 2 ;;
    --skip-gpu-operator)  skip_gpu=true; shift ;;
    --skip-kai-scheduler) skip_kai=true; shift ;;
    --delete-namespaces)  delete_namespaces=true; shift ;;
    --delete-crds)        delete_crds=true; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
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
  print_kv "GPU Operator" "$([[ $skip_gpu == true ]] && echo 'Skip' || echo 'Uninstall')"
  print_kv "KAI Scheduler" "$([[ $skip_kai == true ]] && echo 'Skip' || echo 'Uninstall')"
  print_kv "Delete Namespaces" "$delete_namespaces"
  print_kv "Delete CRDs" "$delete_crds"
  exit 0
fi

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Uninstall KAI Scheduler
#------------------------------------------------------------------------------

if [[ "$skip_kai" == "false" ]]; then
  section "Uninstall KAI Scheduler"

  if helm status kai-scheduler -n "$NS_KAI_SCHEDULER" &>/dev/null; then
    info "Uninstalling KAI Scheduler..."
    helm uninstall kai-scheduler -n "$NS_KAI_SCHEDULER" --wait --timeout "$TIMEOUT_DEPLOY"
    info "KAI Scheduler uninstalled"
  else
    info "KAI Scheduler not found, skipping..."
  fi

  if [[ "$delete_namespaces" == "true" ]]; then
    if kubectl get namespace "$NS_KAI_SCHEDULER" &>/dev/null; then
      info "Deleting namespace '$NS_KAI_SCHEDULER'..."
      kubectl delete namespace "$NS_KAI_SCHEDULER" --ignore-not-found --timeout=60s || true
    fi
  fi
else
  info "Skipping KAI Scheduler (--skip-kai-scheduler)"
fi

#------------------------------------------------------------------------------
# Uninstall GPU Operator
#------------------------------------------------------------------------------

if [[ "$skip_gpu" == "false" ]]; then
  section "Uninstall GPU Operator"

  # Remove metrics scraping configurations first
  if kubectl get crd podmonitors.monitoring.coreos.com &>/dev/null; then
    info "Removing GPU PodMonitor..."
    kubectl delete -f "$MANIFESTS_DIR/gpu-podmonitor.yaml" --ignore-not-found 2>/dev/null || true
  fi
  if kubectl get configmap ama-metrics-prometheus-config -n kube-system &>/dev/null; then
    info "Removing Azure Monitor DCGM scrape config..."
    kubectl delete -f "$MANIFESTS_DIR/ama-metrics-dcgm-scrape.yaml" --ignore-not-found 2>/dev/null || true
  fi

  if helm status gpu-operator -n "$NS_GPU_OPERATOR" &>/dev/null; then
    info "Uninstalling GPU Operator..."
    helm uninstall gpu-operator -n "$NS_GPU_OPERATOR" --wait --timeout "$TIMEOUT_DEPLOY"
    info "GPU Operator uninstalled"
  else
    info "GPU Operator not found, skipping..."
  fi

  if [[ "$delete_crds" == "true" ]]; then
    info "Deleting GPU Operator CRDs..."
    kubectl delete crd clusterpolicies.nvidia.com --ignore-not-found 2>/dev/null || true
    kubectl delete crd nvidiadrivers.nvidia.com --ignore-not-found 2>/dev/null || true
  fi

  if [[ "$delete_namespaces" == "true" ]]; then
    if kubectl get namespace "$NS_GPU_OPERATOR" &>/dev/null; then
      info "Deleting namespace '$NS_GPU_OPERATOR'..."
      kubectl delete namespace "$NS_GPU_OPERATOR" --ignore-not-found --timeout=60s || true
    fi
  fi
else
  info "Skipping GPU Operator (--skip-gpu-operator)"
fi

#------------------------------------------------------------------------------
# Verification
#------------------------------------------------------------------------------
section "Verification"

if [[ "$skip_gpu" == "false" ]]; then
  if helm status gpu-operator -n "$NS_GPU_OPERATOR" &>/dev/null; then
    warn "GPU Operator release still exists"
  else
    info "GPU Operator release removed"
  fi
fi

if [[ "$skip_kai" == "false" ]]; then
  if helm status kai-scheduler -n "$NS_KAI_SCHEDULER" &>/dev/null; then
    warn "KAI Scheduler release still exists"
  else
    info "KAI Scheduler release removed"
  fi
fi

if [[ "$delete_namespaces" == "true" ]]; then
  for ns in "$NS_GPU_OPERATOR" "$NS_KAI_SCHEDULER"; do
    if kubectl get namespace "$ns" &>/dev/null; then
      warn "Namespace '$ns' still exists (may be terminating)"
    else
      info "Namespace '$ns' removed"
    fi
  done
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Uninstall Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "GPU Operator" "$([[ $skip_gpu == true ]] && echo 'Skipped' || echo 'Uninstalled')"
print_kv "KAI Scheduler" "$([[ $skip_kai == true ]] && echo 'Skipped' || echo 'Uninstalled')"
print_kv "Namespaces" "$([[ $delete_namespaces == true ]] && echo 'Deleted' || echo 'Preserved')"
print_kv "CRDs" "$([[ $delete_crds == true ]] && echo 'Deleted' || echo 'Preserved')"
echo
helm list -A | grep -E "gpu-operator|kai-scheduler" || info "No robotics charts remaining"

info "To reinstall, run: ../01-deploy-robotics-charts.sh"

info "Robotics charts uninstall complete"
