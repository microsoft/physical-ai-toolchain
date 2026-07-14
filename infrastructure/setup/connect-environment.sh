#!/usr/bin/env bash
# Configure local Azure, AKS, and OSMO connectivity from a downloaded environment bundle.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME [OPTIONS]

Configure isolated AKS and OSMO client profiles from a downloaded environment bundle.
This script changes only local Azure CLI context and local credential files. It performs read-only service checks.

OPTIONS:
    -h, --help                Show this help message
    -e, --environment NAME    Environment bundle name (required)
    --bundle-dir DIR          Downloaded bundle directory
                              (default: ~/.config/physical-ai-toolchain/environments/<environment>)
    --kubeconfig PATH         Isolated AKS kubeconfig output
    --context NAME            Explicit AKS context (default: cluster name)
    --osmo-config-dir DIR     Isolated OSMO profile directory
    --osmo-method METHOD      OSMO login method: code, dev, password, or token (default: code)
    --osmo-username NAME      Username for dev or password login
    --password-file PATH      Protected password file for password login
    --token-file PATH         Protected token file for token login
    --skip-aks               Skip AKS credential setup
    --skip-osmo              Skip OSMO login
    --config-preview          Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001
    $(basename "$0") --environment dev-001 --osmo-method dev --osmo-username guest
    $(basename "$0") --environment dev-001 --skip-aks --osmo-method token --token-file /protected/osmo.token
EOF
}

# Defaults
environment=""
bundle_dir=""
kubeconfig=""
context=""
osmo_config_dir=""
osmo_method="code"
osmo_username=""
password_file=""
token_file=""
skip_aks=false
skip_osmo=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    -e|--environment)   environment="$2"; shift 2 ;;
    --bundle-dir)       bundle_dir="$2"; shift 2 ;;
    --kubeconfig)       kubeconfig="$2"; shift 2 ;;
    --context)          context="$2"; shift 2 ;;
    --osmo-config-dir)  osmo_config_dir="$2"; shift 2 ;;
    --osmo-method)      osmo_method="$2"; shift 2 ;;
    --osmo-username)    osmo_username="$2"; shift 2 ;;
    --password-file)    password_file="$2"; shift 2 ;;
    --token-file)       token_file="$2"; shift 2 ;;
    --skip-aks)         skip_aks=true; shift ;;
    --skip-osmo)        skip_osmo=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq

[[ -n "$environment" ]] || fatal "--environment is required"
[[ "$environment" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || \
  fatal "Environment must use lowercase letters, numbers, and internal hyphens"
[[ "$osmo_method" =~ ^(code|dev|password|token)$ ]] || fatal "Unsupported OSMO login method: $osmo_method"
[[ "$osmo_method" != "dev" || -n "$osmo_username" ]] || fatal "--osmo-username is required with --osmo-method dev"
[[ "$osmo_method" != "password" || -n "$osmo_username" ]] || fatal "--osmo-username is required with --osmo-method password"
[[ "$osmo_method" != "password" || -n "$password_file" ]] || fatal "--password-file is required with --osmo-method password"
[[ "$osmo_method" != "token" || -n "$token_file" ]] || fatal "--token-file is required with --osmo-method token"

bundle_dir="${bundle_dir:-$HOME/.config/physical-ai-toolchain/environments/$environment}"
require_protected_directory "$bundle_dir"
deployment_file="$bundle_dir/deployment.json"
require_protected_file "$deployment_file"

jq -e --arg environment "$environment" '
  .schema_version == 1 and
  .environment == $environment and
  (.subscription_id | type == "string" and length > 0) and
  (.resource_group | type == "string" and length > 0) and
  (.aks_cluster | type == "string" and length > 0) and
  (.aks_resource_id | type == "string" and length > 0) and
  (.osmo_service_url | type == "string" and length > 0)
' "$deployment_file" >/dev/null || fatal "Invalid deployment metadata: $deployment_file"

subscription_id=$(jq -r '.subscription_id' "$deployment_file")
resource_group=$(jq -r '.resource_group' "$deployment_file")
aks_cluster=$(jq -r '.aks_cluster' "$deployment_file")
aks_resource_id=$(jq -r '.aks_resource_id' "$deployment_file")
osmo_service_url=$(jq -r '.osmo_service_url' "$deployment_file")
[[ "$osmo_service_url" =~ ^http://([0-9]{1,3}\.){3}[0-9]{1,3}(:[0-9]+)?/?$ ]] || \
  fatal "OSMO service URL must use an RFC1918 IPv4 address over HTTP"
osmo_service_host="${osmo_service_url#http://}"
osmo_service_host="${osmo_service_host%%:*}"
osmo_service_host="${osmo_service_host%/}"
is_rfc1918_ipv4 "$osmo_service_host" || fatal "OSMO service URL must use an RFC1918 IPv4 address"
kubeconfig="${kubeconfig:-$HOME/.kube/physical-ai-toolchain/${aks_cluster}.yaml}"
context="${context:-$aks_cluster}"
osmo_config_dir="${osmo_config_dir:-$HOME/.config/physical-ai-toolchain/osmo/$environment}"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Environment" "$environment"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "AKS Cluster" "$aks_cluster"
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Context" "$context"
  print_kv "OSMO URL" "$osmo_service_url"
  print_kv "OSMO Config" "$osmo_config_dir"
  print_kv "OSMO Method" "$osmo_method"
  print_kv "AKS" "$([[ $skip_aks == true ]] && echo skipped || echo configured)"
  print_kv "OSMO" "$([[ $skip_osmo == true ]] && echo skipped || echo configured)"
  exit 0
fi

az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
az account set --subscription "$subscription_id"
actual_subscription=$(az account show --query id -o tsv)
[[ "$actual_subscription" == "$subscription_id" ]] || fatal "Azure CLI did not select the expected subscription"
resource_group_id=$(az group show --subscription "$subscription_id" --name "$resource_group" --query id -o tsv)
[[ -n "$resource_group_id" ]] || fatal "Cannot access Azure resource group $resource_group"

#------------------------------------------------------------------------------
# Configure AKS Connectivity
#------------------------------------------------------------------------------

if [[ "$skip_aks" == "false" || "$skip_osmo" == "false" ]]; then
  require_tools kubectl
  live_aks_resource_id=$(az aks show --subscription "$subscription_id" --resource-group "$resource_group" \
    --name "$aks_cluster" --query id -o tsv)
  [[ "$(printf '%s' "$live_aks_resource_id" | tr '[:upper:]' '[:lower:]')" == \
    "$(printf '%s' "$aks_resource_id" | tr '[:upper:]' '[:lower:]')" ]] || \
    fatal "Live AKS resource does not match deployment metadata"
  if [[ "$skip_aks" == "false" ]]; then
    verify_existing_aks_kubeconfig "$kubeconfig" "$context" "$aks_resource_id"
    connect_aks "$resource_group" "$aks_cluster" "$kubeconfig" "$context"
  else
    verify_existing_aks_kubeconfig "$kubeconfig" "$context" "$aks_resource_id"
    verify_kube_target "$kubeconfig" "$context" aks
  fi

  if [[ "$skip_osmo" == "false" ]]; then
    discovered_osmo_service_url=$(detect_service_url "$kubeconfig" "$context")
    [[ "${discovered_osmo_service_url%/}" == "${osmo_service_url%/}" ]] || \
      fatal "OSMO service URL does not match the AKS internal LoadBalancer"
  fi
fi

#------------------------------------------------------------------------------
# Configure OSMO Connectivity
#------------------------------------------------------------------------------

if [[ "$skip_osmo" == "false" ]]; then
  require_tools curl osmo
  require_no_symlink_path "$osmo_config_dir"
  [[ ! -L "$osmo_config_dir" ]] || fatal "OSMO config directory must not be a symlink: $osmo_config_dir"
  if [[ -d "$osmo_config_dir" ]]; then
    require_protected_directory "$osmo_config_dir"
  else
    mkdir -p "$osmo_config_dir"
    chmod 0700 "$osmo_config_dir"
  fi
  require_protected_directory "$osmo_config_dir"

  curl --fail --silent --show-error --connect-timeout 5 "${osmo_service_url%/}/api/version" >/dev/null || \
    fatal "Cannot reach OSMO at $osmo_service_url; connect the HiL machine to the private network or VPN"

  export XDG_CONFIG_HOME="$osmo_config_dir"
  login_args=(login "${osmo_service_url%/}/" --method "$osmo_method")
  [[ -n "$osmo_username" ]] && login_args+=(--username "$osmo_username")
  if [[ -n "$password_file" ]]; then
    require_protected_file "$password_file"
    login_args+=(--password-file "$password_file")
  fi
  if [[ -n "$token_file" ]]; then
    require_protected_file "$token_file"
    login_args+=(--token-file "$token_file")
  fi
  osmo "${login_args[@]}"
  osmo pool list --format-type json >/dev/null
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Environment" "$environment"
print_kv "Resource Group" "$resource_group"
print_kv "AKS Context" "$([[ $skip_aks == true ]] && echo skipped || echo "$context")"
print_kv "Kubeconfig" "$([[ $skip_aks == true ]] && echo skipped || echo "$kubeconfig")"
print_kv "OSMO URL" "$osmo_service_url"
print_kv "OSMO Config" "$([[ $skip_osmo == true ]] && echo skipped || echo "$osmo_config_dir")"
info "Local environment connectivity configured"
