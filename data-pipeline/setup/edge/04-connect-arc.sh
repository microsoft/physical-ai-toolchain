#!/usr/bin/env bash
# Optionally connect the Ubuntu host and K3s cluster to Azure Arc using interactive authentication.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Connect the Ubuntu server, K3s cluster, or both to Azure Arc. This script uses
interactive/device-code authentication and never accepts a client secret.

OPTIONS:
    -h, --help                    Show this help message
    --subscription-id ID         Azure subscription ID (required)
    --tenant-id ID               Microsoft Entra tenant ID (required)
    --resource-group NAME        Existing Arc resource group (required)
    --location LOCATION          Azure location for Arc metadata (required)
    --server-name NAME           Arc-enabled server resource name
    --cluster-name NAME          Arc-enabled Kubernetes resource name
    --kubeconfig PATH            Protected K3s kubeconfig
    --context NAME               Explicit K3s context
    --enable-server              Connect and verify Arc-enabled server
    --enable-kubernetes          Connect and verify Arc-enabled Kubernetes
    --enable-workload-identity   Enable Arc OIDC and workload identity on K3s
    --config-preview             Print configuration and exit

EXAMPLES:
    $(basename "$0") --subscription-id <id> --tenant-id <id> \
      --resource-group rg-edge --location westus2 \
      --server-name hil-lab-01 --enable-server

    $(basename "$0") --subscription-id <id> --tenant-id <id> \
      --resource-group rg-edge --location westus2 \
      --cluster-name hil-lab-01-k3s --kubeconfig /protected/k3s.yaml \
      --context physical-ai-edge --enable-kubernetes
EOF
}

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
tenant_id="${AZURE_TENANT_ID:-}"
resource_group="${ARC_RESOURCE_GROUP:-}"
location="${ARC_LOCATION:-}"
server_name="${ARC_SERVER_NAME:-}"
cluster_name="${ARC_CLUSTER_NAME:-}"
kubeconfig="${EDGE_KUBECONFIG:-}"
context="$EDGE_K3S_CONTEXT"
enable_server=false
enable_kubernetes=false
enable_workload_identity=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                   show_help; exit 0 ;;
    --subscription-id)           subscription_id="$2"; shift 2 ;;
    --tenant-id)                 tenant_id="$2"; shift 2 ;;
    --resource-group)            resource_group="$2"; shift 2 ;;
    --location)                  location="$2"; shift 2 ;;
    --server-name)               server_name="$2"; shift 2 ;;
    --cluster-name)              cluster_name="$2"; shift 2 ;;
    --kubeconfig)                kubeconfig="$2"; shift 2 ;;
    --context)                   context="$2"; shift 2 ;;
    --enable-server)             enable_server=true; shift ;;
    --enable-kubernetes)         enable_kubernetes=true; shift ;;
    --enable-workload-identity)  enable_workload_identity=true; shift ;;
    --config-preview)            config_preview=true; shift ;;
    *)                           fatal "Unknown option: $1" ;;
  esac
done

[[ "$enable_server" == "true" || "$enable_kubernetes" == "true" ]] || \
  fatal "Select --enable-server, --enable-kubernetes, or both"
[[ "$enable_workload_identity" == "false" || "$enable_kubernetes" == "true" ]] || \
  fatal "--enable-workload-identity requires --enable-kubernetes"
[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$location" ]] || fatal "--location is required"
[[ "$enable_server" == "false" || -n "$server_name" ]] || fatal "--server-name is required"
[[ "$enable_kubernetes" == "false" || -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ "$enable_kubernetes" == "false" || -n "$kubeconfig" ]] || fatal "--kubeconfig is required"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Tenant" "$tenant_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Location" "$location"
  print_kv "Arc Server" "$([[ $enable_server == true ]] && echo "$server_name" || echo skipped)"
  print_kv "Arc Kubernetes" "$([[ $enable_kubernetes == true ]] && echo "$cluster_name" || echo skipped)"
  print_kv "Kubeconfig" "${kubeconfig:-not used}"
  print_kv "Context" "${context:-not used}"
  print_kv "Workload Identity" "$enable_workload_identity"
  print_kv "Authentication" "interactive/device code; no client secret accepted"
  exit 0
fi

require_tools az jq sudo
az account show >/dev/null 2>&1 || fatal "Authenticate Azure CLI before Arc onboarding"
active_subscription=$(az account show --query id -o tsv)
active_tenant=$(az account show --query tenantId -o tsv)
[[ "$active_subscription" == "$subscription_id" ]] || fatal "Active subscription does not match --subscription-id"
[[ "$active_tenant" == "$tenant_id" ]] || fatal "Active tenant does not match --tenant-id"
az group show --name "$resource_group" --subscription "$subscription_id" >/dev/null || fatal "Resource group not found: $resource_group"

if [[ "$enable_server" == "true" ]]; then
  section "Connect Arc-Enabled Server"
  if ! command -v azcmagent >/dev/null 2>&1; then
    [[ -r /etc/os-release ]] || fatal "/etc/os-release is unavailable"
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ "${ID:-}" == "ubuntu" ]] || fatal "Automatic azcmagent installation supports Ubuntu only"
    require_tools apt-get curl dpkg install
    package_file=$(mktemp --suffix=.deb)
    # pinning-ignore: distribution-specific bootstrap package is verified by the Microsoft signed apt repository
    curl -fsSL "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/packages-microsoft-prod.deb" -o "$package_file"
    sudo dpkg -i "$package_file"
    rm -f "$package_file"
    sudo apt-get update
    sudo apt-get install -y azcmagent
  fi
  current_server_id=$(sudo azcmagent show --json 2>/dev/null | jq -r '.resourceId // empty' || true)
  expected_server_id="/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.HybridCompute/machines/${server_name}"
  if [[ -n "$current_server_id" ]]; then
    [[ "$(printf '%s' "$current_server_id" | tr '[:upper:]' '[:lower:]')" == \
      "$(printf '%s' "$expected_server_id" | tr '[:upper:]' '[:lower:]')" ]] || \
      fatal "Arc agent is already connected to an unexpected resource: $current_server_id"
    info "Arc-enabled server is already connected to the expected resource"
  else
    sudo azcmagent connect --subscription-id "$subscription_id" --tenant-id "$tenant_id" \
      --resource-group "$resource_group" --location "$location" --resource-name "$server_name" \
      --use-device-code --cloud AzureCloud --tags 'ArcSQLServerExtensionDeployment=Disabled'
  fi
  server_status=$(sudo azcmagent show --json | jq -r '.status // empty')
  [[ "$(printf '%s' "$server_status" | tr '[:upper:]' '[:lower:]')" == "connected" ]] || \
    fatal "Arc-enabled server status is not Connected"
fi

if [[ "$enable_kubernetes" == "true" ]]; then
  section "Connect Arc-Enabled Kubernetes"
  require_tools kubectl
  verify_kube_target "$kubeconfig" "$context" k3s
  require_az_extension connectedk8s
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

  if [[ "$enable_workload_identity" == "true" ]]; then
    oidc_issuer=$(az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" --query oidcIssuerProfile.issuerUrl -o tsv)
    [[ -n "$oidc_issuer" ]] || fatal "Arc OIDC issuer is unavailable"
    tmp_config=$(mktemp)
    trap 'rm -f "$tmp_config"' EXIT
    sudo cat /etc/rancher/k3s/config.yaml | tee "$tmp_config" >/dev/null
    if grep -q 'service-account-issuer=' "$tmp_config"; then
      grep -Fq "service-account-issuer=$oidc_issuer" "$tmp_config" || \
        fatal "K3s is configured with a different service-account issuer"
    else
      grep -q '^kube-apiserver-arg:' "$tmp_config" && \
        fatal "K3s already defines kube-apiserver-arg; merge the Arc issuer settings explicitly"
      cat >> "$tmp_config" <<EOF
kube-apiserver-arg:
  - "service-account-issuer=$oidc_issuer"
  - "service-account-max-token-expiration=24h"
EOF
      sudo install -m 0600 "$tmp_config" /etc/rancher/k3s/config.yaml
      sudo systemctl restart k3s
      verify_kube_target "$kubeconfig" "$context" k3s
    fi
  fi

  cluster_json=""
  for ((attempt = 1; attempt <= 60; attempt++)); do
    cluster_json=$(az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" -o json)
    [[ "$(jq -r '.provisioningState' <<< "$cluster_json")" == "Succeeded" && \
       "$(jq -r '.connectivityStatus' <<< "$cluster_json")" == "Connected" ]] && break
    (( attempt == 60 )) && fatal "Arc-enabled Kubernetes did not reach Succeeded/Connected within ten minutes"
    sleep 10
  done
  expected_cluster_id="/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Kubernetes/connectedClusters/${cluster_name}"
  [[ "$(jq -r '.id | ascii_downcase' <<< "$cluster_json")" == \
    "$(printf '%s' "$expected_cluster_id" | tr '[:upper:]' '[:lower:]')" ]] || \
    fatal "Arc-enabled Kubernetes resource ID does not match the requested cluster"
  kube_kubectl "$kubeconfig" "$context" wait --for=condition=Available deployment --all \
    -n azure-arc --timeout=300s
fi

section "Deployment Summary"
print_kv "Subscription" "$subscription_id"
print_kv "Resource Group" "$resource_group"
print_kv "Location" "$location"
print_kv "Arc Server" "$([[ $enable_server == true ]] && echo connected || echo skipped)"
print_kv "Arc Kubernetes" "$([[ $enable_kubernetes == true ]] && echo connected || echo skipped)"
print_kv "Workload Identity" "$enable_workload_identity"
info "Optional Azure Arc onboarding complete"
