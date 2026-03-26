#!/usr/bin/env bash
# Deploy Volcano Scheduler to AKS cluster for advanced ML job scheduling
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

VALUES_DIR="$SCRIPT_DIR/values"

# Volcano-specific defaults
VOLCANO_VERSION="${VOLCANO_VERSION:-1.12.2}"
HELM_REPO_VOLCANO="${HELM_REPO_VOLCANO:-https://volcano-sh.github.io/helm-charts}"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy Volcano Scheduler to an AKS cluster for advanced ML job scheduling.
Optional component - only needed for complex batch scheduling scenarios.

OPTIONS:
    -h, --help                 Show this help message
    -t, --tf-dir DIR           Terraform directory (default: $DEFAULT_TF_DIR)
    --volcano-version VERSION  Volcano version (default: $VOLCANO_VERSION)
    --config-preview           Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --volcano-version 1.12.2
    $(basename "$0") -t /path/to/terraform
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
volcano_version="$VOLCANO_VERSION"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -t|--tf-dir)         tf_dir="$2"; shift 2 ;;
    --volcano-version)   volcano_version="$2"; shift 2 ;;
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
  print_kv "Volcano Scheduler" "$volcano_version"
  print_kv "Namespace" "$NS_AZUREML"
  exit 0
fi

#------------------------------------------------------------------------------
# Validate Required Files
#------------------------------------------------------------------------------

volcano_values="$VALUES_DIR/volcano-sh-values.yaml"
[[ -f "$volcano_values" ]] || fatal "Volcano values not found: $volcano_values"

#------------------------------------------------------------------------------
# Connect and Prepare Cluster
#------------------------------------------------------------------------------
section "Connect and Prepare Cluster"

connect_aks "$rg" "$cluster"

ensure_namespace "$NS_AZUREML"
kubectl create serviceaccount azureml-workload -n "$NS_AZUREML" --dry-run=client -o yaml | kubectl apply -f -

#------------------------------------------------------------------------------
# Install Volcano Scheduler
#------------------------------------------------------------------------------
section "Install Volcano Scheduler $volcano_version"

helm repo add volcano-sh "$HELM_REPO_VOLCANO" 2>/dev/null || true
helm repo update >/dev/null

helm upgrade --install volcano volcano-sh/volcano \
  --namespace "$NS_AZUREML" \
  --version "$volcano_version" \
  -f "$volcano_values" \
  --wait --timeout "$TIMEOUT_DEPLOY"

info "Volcano Scheduler installed successfully"

#------------------------------------------------------------------------------
# Deployment Verification
#------------------------------------------------------------------------------
section "Deployment Verification"

kubectl get pods -n "$NS_AZUREML" -o wide

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "Volcano Scheduler" "$volcano_version"
print_kv "Namespace" "$NS_AZUREML"
echo
helm list -n "$NS_AZUREML" | grep -E "volcano" || true

info "Volcano Scheduler deployment complete"
