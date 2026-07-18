#!/usr/bin/env bash
# Create a resource-group-scoped service principal for optional Azure Arc onboarding.
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load the shared helpers and Azure setup defaults.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

# Describe the required identity inputs and the protected files produced by this command.
show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Create a dedicated Azure Arc onboarding principal and write its credential to a protected file.
The edge connection script uses device-code authentication by default because current Arc CLI
secret flags expose service-principal secrets in process arguments.

OPTIONS:
    -h, --help                   Show this help message
    --subscription-id ID         Azure subscription ID (required)
    --resource-group NAME        Existing target resource group (required)
    --principal-name NAME        Service principal display name (required)
    --credential-file PATH       Protected secret JSON output (required)
    --metadata-file PATH         Protected non-secret metadata JSON output (required)
    --include-kubernetes         Add Kubernetes Cluster - Azure Arc Onboarding
    --config-preview             Print configuration and exit

EXAMPLES:
    $(basename "$0") --subscription-id <id> --resource-group rg-edge \
      --principal-name hil-lab-01-arc-onboarding \
      --credential-file /protected/arc-onboarding.json \
      --metadata-file /protected/arc-onboarding.metadata.json \
      --include-kubernetes
EOF
}

# Initialize required Azure identifiers, output paths, and the optional Kubernetes onboarding role.
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
resource_group="${ARC_RESOURCE_GROUP:-}"
principal_name=""
credential_file=""
metadata_file=""
include_kubernetes=false
config_preview=false

# Apply command-line values before checking that the principal target and output files are safe to use.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --subscription-id)      subscription_id="$2"; shift 2 ;;
    --resource-group)       resource_group="$2"; shift 2 ;;
    --principal-name)       principal_name="$2"; shift 2 ;;
    --credential-file)      credential_file="$2"; shift 2 ;;
    --metadata-file)        metadata_file="$2"; shift 2 ;;
    --include-kubernetes)   include_kubernetes=true; shift ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

# Reject incomplete targets and prevent the secret and metadata documents from overwriting one another.
[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$principal_name" ]] || fatal "--principal-name is required"
[[ -n "$credential_file" ]] || fatal "--credential-file is required"
[[ -n "$metadata_file" ]] || fatal "--metadata-file is required"
[[ "$credential_file" != "$metadata_file" ]] || fatal "Credential and metadata files must be different"

# Show the planned scope and output locations, then exit before authenticating or creating Azure resources.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Principal" "$principal_name"
  print_kv "Credential File" "$credential_file"
  print_kv "Metadata File" "$metadata_file"
  print_kv "Arc Server Role" "Azure Connected Machine Onboarding"
  print_kv "Arc Kubernetes Role" "$([[ $include_kubernetes == true ]] && echo enabled || echo skipped)"
  exit 0
fi

# Verify Azure authentication, subscription selection, resource-group existence, and required local tools.
require_tools az jq
az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated"
active_subscription=$(az account show --query id -o tsv)
[[ "$active_subscription" == "$subscription_id" ]] || fatal "Active subscription $active_subscription does not match $subscription_id"
az group show --name "$resource_group" --subscription "$subscription_id" >/dev/null || fatal "Resource group not found: $resource_group"

scope="/subscriptions/${subscription_id}/resourceGroups/${resource_group}"
umask 077
mkdir -p "$(dirname "$credential_file")" "$(dirname "$metadata_file")"
[[ ! -e "$credential_file" ]] || fatal "Credential file already exists: $credential_file"

# Create the resource-group-scoped service principal and write its secret with owner-only permissions.
section "Create Arc Onboarding Principal"
credential_json=$(az ad sp create-for-rbac --name "$principal_name" \
  --role "Azure Connected Machine Onboarding" --scopes "$scope" --output json)
printf '%s\n' "$credential_json" > "$credential_file"
chmod 0600 "$credential_file"
application_id=$(jq -r '.appId' <<< "$credential_json")
tenant_id=$(jq -r '.tenant' <<< "$credential_json")
[[ -n "$application_id" && "$application_id" != "null" ]] || fatal "Service principal response omitted appId"
principal_id=$(az ad sp show --id "$application_id" --query id -o tsv)

# Add the optional Kubernetes Arc role while retaining the required server onboarding role.
roles=("Azure Connected Machine Onboarding")
if [[ "$include_kubernetes" == "true" ]]; then
  az role assignment create --assignee-object-id "$principal_id" --assignee-principal-type ServicePrincipal \
    --role "Kubernetes Cluster - Azure Arc Onboarding" --scope "$scope" --output none
  roles+=("Kubernetes Cluster - Azure Arc Onboarding")
fi

# Write non-secret metadata that lets later setup steps identify the principal and granted roles.
jq -n \
  --arg subscription_id "$subscription_id" \
  --arg tenant_id "$tenant_id" \
  --arg resource_group "$resource_group" \
  --arg principal_id "$principal_id" \
  --arg application_id "$application_id" \
  --arg created_at "$(date -u +%FT%TZ)" \
  --argjson roles "$(printf '%s\n' "${roles[@]}" | jq -R . | jq -s .)" \
  '{schema_version: 1, subscription_id: $subscription_id, tenant_id: $tenant_id, resource_group: $resource_group, principal_id: $principal_id, application_id: $application_id, roles: $roles, created_at: $created_at}' \
  > "$metadata_file"
chmod 0600 "$metadata_file"
unset credential_json

# Report the created principal and protected output locations without printing the credential contents.
section "Deployment Summary"
print_kv "Principal" "$principal_name"
print_kv "Application ID" "$application_id"
print_kv "Resource Group" "$resource_group"
print_kv "Roles" "${roles[*]}"
print_kv "Credential File" "$credential_file"
print_kv "Metadata File" "$metadata_file"
warn "Store the credential in an approved secret manager; do not pass its password through Arc CLI arguments"
info "Arc onboarding principal created"
