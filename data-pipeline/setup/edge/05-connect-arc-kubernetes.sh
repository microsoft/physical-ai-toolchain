#!/usr/bin/env bash
# Connect the K3s cluster to Azure Arc using an existing Azure CLI session.
# cspell:ignore jwks
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

[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$location" ]] || fatal "--location is required"
[[ -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ -n "$kubeconfig" ]] || fatal "--kubeconfig is required"

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

require_tools az jq sudo
az account show >/dev/null 2>&1 || \
  fatal "Authenticate Azure CLI with device-code login before Arc onboarding"
active_subscription=$(az account show --query id -o tsv)
active_tenant=$(az account show --query tenantId -o tsv)
[[ "$active_subscription" == "$subscription_id" ]] || fatal "Active subscription does not match --subscription-id"
[[ "$active_tenant" == "$tenant_id" ]] || fatal "Active tenant does not match --tenant-id"
az group show --name "$resource_group" --subscription "$subscription_id" >/dev/null || \
  fatal "Resource group not found: $resource_group"

section "Connect Arc-Enabled Kubernetes"
require_tools kubectl
verify_kube_target "$kubeconfig" "$context" k3s
require_az_extension connectedk8s
if [[ "$enable_workload_identity" == "true" ]]; then
  require_tools cmp curl find python3 sed sort
  azure_cli_version=$(az version --query '"azure-cli"' -o tsv)
  connectedk8s_version=$(az extension show --name connectedk8s --query version -o tsv)
  printf '%s\n%s\n' "2.64.0" "$azure_cli_version" | sort -V -C || \
    fatal "Azure CLI $azure_cli_version does not support Arc workload identity; version 2.64.0 or newer is required"
  printf '%s\n%s\n' "1.10.0" "$connectedk8s_version" | sort -V -C || \
    fatal "connectedk8s $connectedk8s_version does not support Arc workload identity; version 1.10.0 or newer is required"
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

if [[ "$enable_workload_identity" == "true" ]]; then
  for ((attempt = 1; attempt <= 60; attempt++)); do
    oidc_issuer=$(az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" --query oidcIssuerProfile.issuerUrl -o tsv 2>/dev/null || true)
    [[ -n "$oidc_issuer" ]] && break
    (( attempt == 60 )) && fatal "Arc OIDC issuer was not available within five minutes"
    sleep 5
  done
  k3s_config=/etc/rancher/k3s/config.yaml
  k3s_config_dir=/etc/rancher/k3s/config.yaml.d
  workload_identity_config="$k3s_config_dir/90-arc-workload-identity.yaml"
  k3s_config_sources=("$k3s_config")
  if [[ -d "$k3s_config_dir" ]]; then
    while IFS= read -r -d '' config_source; do
      [[ "$config_source" != "$workload_identity_config" ]] && k3s_config_sources+=("$config_source")
    done < <(sudo find "$k3s_config_dir" -maxdepth 1 -type f \
      \( -name '*.yaml' -o -name '*.yml' \) -print0)
  fi
  configured_issuers=()
  while IFS= read -r configured_issuer; do
    [[ -n "$configured_issuer" ]] && configured_issuers+=("$configured_issuer")
  done < <(sudo grep -hsE 'service-account-issuer=' "${k3s_config_sources[@]}" 2>/dev/null | \
    sed -E 's/.*service-account-issuer=//; s/["[:space:]]+$//' | sort -u)
  (( ${#configured_issuers[@]} <= 1 )) || fatal "K3s has multiple service-account issuer values"
  if [[ ${#configured_issuers[@]} -eq 1 && "${configured_issuers[0]}" != "$oidc_issuer" ]]; then
    fatal "K3s is configured with a different service-account issuer: ${configured_issuers[0]}"
  fi
  configured_expirations=()
  while IFS= read -r configured_expiration; do
    [[ -n "$configured_expiration" ]] && configured_expirations+=("$configured_expiration")
  done < <(sudo grep -hsE 'service-account-max-token-expiration=' "${k3s_config_sources[@]}" 2>/dev/null | \
    sed -E 's/.*service-account-max-token-expiration=//; s/["[:space:]]+$//' | sort -u)
  (( ${#configured_expirations[@]} <= 1 )) || fatal "K3s has multiple service-account token expiration values"
  if [[ ${#configured_expirations[@]} -eq 1 && "${configured_expirations[0]}" != "24h" ]]; then
    fatal "K3s service-account token expiration is ${configured_expirations[0]}; expected 24h"
  fi

  workload_identity_args=()
  [[ ${#configured_issuers[@]} -eq 0 ]] && workload_identity_args+=("service-account-issuer=$oidc_issuer")
  [[ ${#configured_expirations[@]} -eq 0 ]] && workload_identity_args+=("service-account-max-token-expiration=24h")
  workload_identity_config_changed=false
  if [[ ${#workload_identity_args[@]} -gt 0 ]]; then
    tmp_config=$(mktemp)
    trap 'rm -f "$tmp_config"' EXIT
    printf '%s\n' 'kube-apiserver-arg+:' > "$tmp_config"
    for workload_identity_arg in "${workload_identity_args[@]}"; do
      printf '  - "%s"\n' "$workload_identity_arg" >> "$tmp_config"
    done
    sudo install -d -m 0755 "$k3s_config_dir"
    if ! sudo cmp -s "$tmp_config" "$workload_identity_config"; then
      sudo install -m 0600 "$tmp_config" "$workload_identity_config"
      workload_identity_config_changed=true
    fi
  elif [[ -e "$workload_identity_config" ]]; then
    sudo rm -f "$workload_identity_config"
    workload_identity_config_changed=true
  fi
  if [[ "$workload_identity_config_changed" == "true" ]]; then
    sudo systemctl restart k3s
    verify_kube_target "$kubeconfig" "$context" k3s
  fi

  oidc_document=""
  for ((attempt = 1; attempt <= 60; attempt++)); do
    oidc_document=$(curl -fsSL --connect-timeout 10 \
      "${oidc_issuer%/}/.well-known/openid-configuration" 2>/dev/null || true)
    if jq -e --arg issuer "$oidc_issuer" '
        .issuer == $issuer and
        (.jwks_uri | type == "string" and length > 0)
      ' <<< "$oidc_document" >/dev/null 2>&1; then
      break
    fi
    (( attempt == 60 )) && fatal "Arc OIDC discovery document did not become valid within five minutes"
    sleep 5
  done
  token_issuer=$(kube_kubectl "$kubeconfig" "$context" create token default \
    -n kube-system --duration=10m | python3 -c '
import base64
import json
import sys

token = sys.stdin.read().strip()
payload = token.split(".")[1]
payload += "=" * (-len(payload) % 4)
print(json.loads(base64.urlsafe_b64decode(payload)).get("iss", ""))
')
  [[ "$token_issuer" == "$oidc_issuer" ]] || \
    fatal "K3s service-account token issuer does not match the Arc OIDC issuer"
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
if [[ "$enable_workload_identity" == "true" ]]; then
  jq -e --arg issuer "$oidc_issuer" '
    .oidcIssuerProfile.enabled == true and
    .oidcIssuerProfile.issuerUrl == $issuer and
    .securityProfile.workloadIdentity.enabled == true
  ' <<< "$cluster_json" >/dev/null || fatal "Azure does not report OIDC and workload identity as enabled"
fi
kube_kubectl "$kubeconfig" "$context" wait --for=condition=Available deployment --all \
  -n azure-arc --timeout=300s
if [[ "$enable_workload_identity" == "true" ]]; then
  workload_identity_deployments=0
  for ((attempt = 1; attempt <= 60; attempt++)); do
    workload_identity_deployments=$(kube_kubectl "$kubeconfig" "$context" get deployment \
      -n arc-workload-identity -o json 2>/dev/null | jq '.items | length' || echo 0)
    (( workload_identity_deployments > 0 )) && break
    (( attempt == 60 )) && fatal "Arc workload identity webhook deployment was not created within five minutes"
    sleep 5
  done
  kube_kubectl "$kubeconfig" "$context" wait --for=condition=Available deployment --all \
    -n arc-workload-identity --timeout=300s
fi

section "Deployment Summary"
print_kv "Subscription" "$subscription_id"
print_kv "Resource Group" "$resource_group"
print_kv "Location" "$location"
print_kv "Arc Kubernetes" "connected"
print_kv "Workload Identity" "$enable_workload_identity"
print_kv "OIDC Issuer" "${oidc_issuer:-not configured}"
info "Arc-enabled Kubernetes onboarding complete"
