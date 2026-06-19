#!/usr/bin/env bash
# Deploy NVIDIA GPU Operator and KAI Scheduler to AKS cluster
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

VALUES_DIR="$SCRIPT_DIR/values"
MANIFESTS_DIR="$SCRIPT_DIR/manifests"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy NVIDIA GPU Operator and KAI Scheduler to an AKS cluster.

OPTIONS:
    -h, --help               Show this help message
    -t, --tf-dir DIR         Terraform directory (default: $DEFAULT_TF_DIR)
    --gpu-version VERSION    GPU Operator version (default: $GPU_OPERATOR_VERSION)
    --kai-version VERSION    KAI Scheduler version (default: $KAI_SCHEDULER_VERSION)
    --skip-gpu-operator      Skip GPU Operator installation
    --skip-kai-scheduler     Skip KAI Scheduler installation
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --gpu-version v24.9.1 --kai-version 0.3.0
    $(basename "$0") --skip-kai-scheduler
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
gpu_version="$GPU_OPERATOR_VERSION"
kai_version="$KAI_SCHEDULER_VERSION"
skip_gpu=false
skip_kai=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -t|--tf-dir)          tf_dir="$2"; shift 2 ;;
    --gpu-version)        gpu_version="$2"; shift 2 ;;
    --kai-version)        kai_version="$2"; shift 2 ;;
    --skip-gpu-operator)  skip_gpu=true; shift ;;
    --skip-kai-scheduler) skip_kai=true; shift ;;
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
  print_kv "GPU Operator" "$([[ $skip_gpu == true ]] && echo 'Skipped' || echo "$gpu_version")"
  print_kv "KAI Scheduler" "$([[ $skip_kai == true ]] && echo 'Skipped' || echo "$kai_version")"
  exit 0
fi

#------------------------------------------------------------------------------
# Validate Required Files
#------------------------------------------------------------------------------

gpu_values="$VALUES_DIR/nvidia-gpu-operator.yaml"
kai_values="$VALUES_DIR/kai-scheduler.yaml"

[[ "$skip_gpu" == "true" || -f "$gpu_values" ]] || fatal "GPU Operator values not found: $gpu_values"
[[ "$skip_kai" == "true" || -f "$kai_values" ]] || fatal "KAI Scheduler values not found: $kai_values"

#------------------------------------------------------------------------------
# Connect and Prepare Cluster
#------------------------------------------------------------------------------
section "Connect and Prepare Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Install GPU Operator
#------------------------------------------------------------------------------

if [[ "$skip_gpu" == "false" ]]; then
  section "Install GPU Operator $gpu_version"

  helm repo add nvidia "$HELM_REPO_GPU_OPERATOR" >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1

  gpu_chart_args=( nvidia/gpu-operator --version "$gpu_version" )
  if [[ -n "${GPU_OPERATOR_CHART_SHA256:-}" ]]; then
    gpu_tgz=$(pull_and_verify_chart "nvidia/gpu-operator" "$gpu_version" "$GPU_OPERATOR_CHART_SHA256" "$(mktemp -d)")
    gpu_chart_args=( "$gpu_tgz" )
  fi

  helm upgrade --install gpu-operator "${gpu_chart_args[@]}" \
    --namespace "$NS_GPU_OPERATOR" \
    --create-namespace \
    --disable-openapi-validation \
    -f "$gpu_values" \
    --wait --timeout "$TIMEOUT_DEPLOY"

  # Configure metrics scraping based on available monitoring stack
  if kubectl get crd podmonitors.monitoring.coreos.com &>/dev/null; then
    info "Applying GPU PodMonitor (Prometheus Operator detected)..."
    kubectl apply -f "$MANIFESTS_DIR/gpu-podmonitor.yaml"
  elif kubectl get daemonset ama-metrics -n kube-system &>/dev/null; then
    info "Configuring Azure Monitor Prometheus to scrape DCGM metrics..."
    kubectl apply -f "$MANIFESTS_DIR/ama-metrics-dcgm-scrape.yaml"
  else
    warn "No Prometheus scraping configured - GPU metrics available via direct pod access on port 9400"
  fi

  info "GPU Operator installed successfully"

  # Install Microsoft GRID driver on RTX PRO 6000 nodes (vGPU/SR-IOV)
  # These nodes have nvidia.com/gpu.deploy.driver=false and need the GRID driver
  # instead of the datacenter driver managed by the GPU Operator.
  grid_manifest="$MANIFESTS_DIR/gpu-grid-driver-installer.yaml"
  if [[ -f "$grid_manifest" ]]; then
    rtx_nodes=$(kubectl get nodes -l nvidia.com/gpu.deploy.driver=false -o name 2>/dev/null || true)
    if [[ -n "$rtx_nodes" ]]; then
      info "vGPU nodes detected (nvidia.com/gpu.deploy.driver=false) — applying GRID driver DaemonSet..."
      kubectl apply -f "$grid_manifest"
      info "GRID driver DaemonSet applied. Monitor with: kubectl logs -n gpu-operator -l app.kubernetes.io/name=gpu-grid-driver-installer -c installer"
    fi
  fi
else
  info "Skipping GPU Operator (--skip-gpu-operator)"
fi

#------------------------------------------------------------------------------
# Install KAI Scheduler
#------------------------------------------------------------------------------

if [[ "$skip_kai" == "false" ]]; then
  section "Install KAI Scheduler $kai_version"

  kai_chart_ref="$HELM_REPO_KAI/kai-scheduler"
  kai_chart_args=( "$kai_chart_ref" --version "$kai_version" )
  if [[ -n "${KAI_SCHEDULER_CHART_SHA256:-}" ]]; then
    kai_tgz=$(pull_and_verify_chart "$kai_chart_ref" "$kai_version" "$KAI_SCHEDULER_CHART_SHA256" "$(mktemp -d)")
    kai_chart_args=( "$kai_tgz" )
  fi

  # KAI uses an operator pattern: the chart deploys kai-operator, which then
  # creates the scheduler/binder/admission components and a cluster-scoped
  # SchedulingShard CR. That CR holds a permanent Reconciling=True condition,
  # which Helm v4's kstatus-based --wait reads as "in progress", so --wait always
  # times out despite a healthy deployment. Wait on KAI's own signals instead.
  helm upgrade --install kai-scheduler "${kai_chart_args[@]}" \
    --namespace "$NS_KAI_SCHEDULER" \
    --create-namespace \
    -f "$kai_values" \
    --timeout "$TIMEOUT_DEPLOY"

  info "Waiting for KAI operator rollout..."
  kubectl rollout status deployment/kai-operator -n "$NS_KAI_SCHEDULER" --timeout "$TIMEOUT_DEPLOY"

  info "Waiting for KAI SchedulingShard to become available..."
  kubectl wait --for=condition=Available schedulingshard/default --timeout "$TIMEOUT_DEPLOY"

  info "KAI Scheduler installed successfully"
else
  info "Skipping KAI Scheduler (--skip-kai-scheduler)"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "GPU Operator" "$([[ $skip_gpu == true ]] && echo 'Skipped' || echo "$gpu_version")"
print_kv "KAI Scheduler" "$([[ $skip_kai == true ]] && echo 'Skipped' || echo "$kai_version")"
echo
helm list -A | grep -E "gpu-operator|kai-scheduler" || true

info "Robotics charts deployment complete"
