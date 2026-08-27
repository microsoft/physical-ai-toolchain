#!/usr/bin/env bash
# Connect the K3s cluster to Azure Arc using an existing Azure CLI session.
# cspell:ignore jwks
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared helpers plus the Arc and K3s defaults.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

# Describe the Azure Arc target, protected kubeconfig, and optional workload identity behavior.
show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Connect a K3s cluster to Azure Arc using an existing Azure CLI session.

OPTIONS:
    -h, --help                    Show this help message
    --subscription-id ID         Azure subscription ID (required)
    --tenant-id ID               Microsoft Entra tenant ID (required)
    --resource-group NAME        Existing Arc resource group (required)
    --location LOCATION          Azure location for Arc metadata (required)
    --cluster-name NAME          Arc-enabled Kubernetes resource name (required)
    --kubeconfig PATH            Protected K3s kubeconfig (required)
    --context NAME               Explicit K3s context
    --enable-workload-identity   Enable Arc OIDC and workload identity on K3s
    --config-preview             Print configuration and exit

EXAMPLES:
    $(basename "$0") --subscription-id <id> --tenant-id <id> \
      --resource-group rg-edge --location westus2 \
      --cluster-name hil-lab-01-k3s --kubeconfig /protected/k3s.yaml \
      --context physical-ai-edge --enable-workload-identity
EOF
}

# Initialize Azure identifiers, the K3s target, and the optional OIDC/workload identity switch.
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
tenant_id="${AZURE_TENANT_ID:-}"
resource_group="${ARC_RESOURCE_GROUP:-}"
location="${ARC_LOCATION:-}"
cluster_name="${ARC_CLUSTER_NAME:-}"
kubeconfig="${EDGE_KUBECONFIG:-}"
context="$EDGE_K3S_CONTEXT"
enable_workload_identity=false
config_preview=false
oidc_issuer=""

# Apply command-line values before validating the Azure, K3s, and workload identity targets.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                   show_help; exit 0 ;;
    --subscription-id)           subscription_id="$2"; shift 2 ;;
    --tenant-id)                 tenant_id="$2"; shift 2 ;;
    --resource-group)            resource_group="$2"; shift 2 ;;
    --location)                  location="$2"; shift 2 ;;
    --cluster-name)              cluster_name="$2"; shift 2 ;;
    --kubeconfig)                kubeconfig="$2"; shift 2 ;;
    --context)                   context="$2"; shift 2 ;;
    --enable-workload-identity)  enable_workload_identity=true; shift ;;
    --config-preview)            config_preview=true; shift ;;
    *)                           fatal "Unknown option: $1" ;;
  esac
done

# Reject incomplete Azure and kubeconfig targets before the network preflight or Arc mutation.
[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$location" ]] || fatal "--location is required"
[[ -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ -n "$kubeconfig" ]] || fatal "--kubeconfig is required"

# Show the planned Arc connection and preflight behavior, then exit without contacting Azure or Kubernetes.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Tenant" "$tenant_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Location" "$location"
  print_kv "Arc Kubernetes" "$cluster_name"
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Context" "$context"
  print_kv "Workload Identity" "$enable_workload_identity"
  print_kv "Authentication" "Azure CLI session; device-code login supported"
  exit 0
fi

# Check the commands used by the setup path.
require_tools az kubectl sudo

  # Connect or update the Arc-enabled Kubernetes resource using the selected workload identity options.
section "Connect Arc-Enabled Kubernetes"
require_az_extension connectedk8s
if [[ "$enable_workload_identity" == "true" ]]; then
  require_tools cmp
fi
export KUBECONFIG="$kubeconfig"
connect_args=(
  connectedk8s connect
  --name "$cluster_name"
  --resource-group "$resource_group"
  --location "$location"
  --subscription "$subscription_id"
  --kube-config "$kubeconfig"
  --kube-context "$context"
)
if [[ "$enable_workload_identity" == "true" ]]; then
  connect_args+=(--enable-oidc-issuer --enable-workload-identity)
fi

if az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
    --subscription "$subscription_id" >/dev/null 2>&1; then
  info "Arc-enabled Kubernetes resource already exists"
  if [[ "$enable_workload_identity" == "true" ]]; then
    az connectedk8s update --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" --kube-config "$kubeconfig" --kube-context "$context" \
      --enable-oidc-issuer --enable-workload-identity --output none
  fi
else
  az "${connect_args[@]}" --output none
fi

# When requested, align K3s token settings with the Arc OIDC issuer.
if [[ "$enable_workload_identity" == "true" ]]; then
  for ((attempt = 1; attempt <= 60; attempt++)); do
    oidc_issuer=$(az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" --query oidcIssuerProfile.issuerUrl -o tsv 2>/dev/null || true)
    [[ -n "$oidc_issuer" ]] && break
    (( attempt == 60 )) && fatal "Arc OIDC issuer was not available within five minutes"
    sleep 5
  done
  k3s_config_dir=/etc/rancher/k3s/config.yaml.d
  workload_identity_config="$k3s_config_dir/90-arc-workload-identity.yaml"
  tmp_config=$(mktemp)
  trap 'rm -f "$tmp_config"' EXIT
  cat > "$tmp_config" <<EOF
kube-apiserver-arg+:
  - "service-account-issuer=$oidc_issuer"
  - "service-account-max-token-expiration=24h"
EOF
  sudo install -d -m 0755 "$k3s_config_dir"
  if ! sudo cmp -s "$tmp_config" "$workload_identity_config"; then
    sudo install -m 0600 "$tmp_config" "$workload_identity_config"
    sudo systemctl restart k3s
  fi
fi

# Report the connected Arc Kubernetes resource and whether workload identity was enabled.
section "Deployment Summary"
print_kv "Subscription" "$subscription_id"
print_kv "Resource Group" "$resource_group"
print_kv "Location" "$location"
print_kv "Arc Kubernetes" "connected"
print_kv "Workload Identity" "$enable_workload_identity"
print_kv "OIDC Issuer" "${oidc_issuer:-not configured}"
info "Arc-enabled Kubernetes onboarding complete"
