#!/usr/bin/env bash
# Connect the Ubuntu host to Azure Arc using interactive device-code authentication.
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

Connect the Ubuntu server to Azure Arc using an existing Azure CLI session.
The Arc agent uses device-code authentication when connecting the server.

OPTIONS:
    -h, --help                    Show this help message
    --subscription-id ID         Azure subscription ID (required)
    --tenant-id ID               Microsoft Entra tenant ID (required)
    --resource-group NAME        Existing Arc resource group (required)
    --location LOCATION          Azure location for Arc metadata (required)
    --server-name NAME           Arc-enabled server resource name (required)
    --config-preview             Print configuration and exit

EXAMPLES:
    $(basename "$0") --subscription-id <id> --tenant-id <id> \
      --resource-group rg-edge --location westus2 \
      --server-name hil-lab-01
EOF
}

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
tenant_id="${AZURE_TENANT_ID:-}"
resource_group="${ARC_RESOURCE_GROUP:-}"
location="${ARC_LOCATION:-}"
server_name="${ARC_SERVER_NAME:-}"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    --subscription-id)    subscription_id="$2"; shift 2 ;;
    --tenant-id)          tenant_id="$2"; shift 2 ;;
    --resource-group)     resource_group="$2"; shift 2 ;;
    --location)           location="$2"; shift 2 ;;
    --server-name)        server_name="$2"; shift 2 ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$location" ]] || fatal "--location is required"
[[ -n "$server_name" ]] || fatal "--server-name is required"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Tenant" "$tenant_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Location" "$location"
  print_kv "Arc Server" "$server_name"
  print_kv "Authentication" "Azure CLI session and device code"
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

section "Deployment Summary"
print_kv "Subscription" "$subscription_id"
print_kv "Resource Group" "$resource_group"
print_kv "Location" "$location"
print_kv "Arc Server" "connected"
info "Arc-enabled Server onboarding complete"
