#!/usr/bin/env bash
# Plan, apply, or remove the Azure access that local OSMO workflows and the local dataviewer use.
# cspell:ignore connectedclusters managedidentity userassignedidentities
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../data-pipeline/setup/defaults.conf
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"
# shellcheck source=../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") --tenant-id ID --subscription ID --arc-cluster-resource-id ID
         --workflow-identity-id ID [OPTIONS]

Plan the Azure access that lets local OSMO workflows and the local dataviewer use Azure ML tracking,
Blob Storage, and ACR images from this K3s cluster. Without --apply or --remove, the script only reads
Azure and prints what --apply would create.

Azure resources the script manages:
  - a federated credential on the workflow identity that trusts this cluster's Arc OIDC issuer for
    system:serviceaccount:$LOCAL_OSMO_WORKFLOWS_NAMESPACE:osmo-workflow
  - with --dataviewer-identity-id, the same for
    system:serviceaccount:$LOCAL_DATAVIEWER_NAMESPACE:dataviewer-backend
  - with --acr-name, a pull-only ACR token, stored in the local OSMO as a REGISTRY credential because
    kubelet image pulls cannot use workload identity

Role assignments are reported, never changed. The cluster uses the identities only after you redeploy
with the commands the script prints.

OPTIONS:
    -h, --help                     Show this help message
    --tenant-id ID                 Expected Microsoft Entra tenant (required)
    --subscription ID              Expected Azure subscription (required)
    --arc-cluster-resource-id ID   Arc-enabled Kubernetes resource of this cluster (required)
    --workflow-identity-id ID      User-assigned identity for OSMO workflow pods (required)
    --dataviewer-identity-id ID    User-assigned identity for the dataviewer's Blob access
    --storage-account NAME         Storage account the dataviewer lists (needs --dataviewer-identity-id)
    --acr-name NAME                Container registry that OSMO tasks pull images from
    --acr-token-days DAYS          Lifetime of the ACR pull token password (default: 30)
    --apply                        Create the missing federated credentials, and the ACR token
    --remove                       Delete the federated credentials, ACR token, and OSMO credential
    --config-preview               Print configuration and exit

EXAMPLES:
    $(basename "$0") --tenant-id <tenant> --subscription <subscription> \\
      --arc-cluster-resource-id <arc-cluster-id> --workflow-identity-id <identity-id> --config-preview
    $(basename "$0") --tenant-id <tenant> --subscription <subscription> \\
      --arc-cluster-resource-id <arc-cluster-id> --workflow-identity-id <identity-id> --acr-name <registry>
EOF
}

tenant_id=""
subscription_id=""
arc_cluster_resource_id=""
workflow_identity_id=""
dataviewer_identity_id=""
storage_account=""
acr_name=""
acr_token_days=30
mode=plan
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                  show_help; exit 0 ;;
    --tenant-id)                tenant_id="$2"; shift 2 ;;
    --subscription)             subscription_id="$2"; shift 2 ;;
    --arc-cluster-resource-id)  arc_cluster_resource_id="$2"; shift 2 ;;
    --workflow-identity-id)     workflow_identity_id="$2"; shift 2 ;;
    --dataviewer-identity-id)   dataviewer_identity_id="$2"; shift 2 ;;
    --storage-account)          storage_account="$2"; shift 2 ;;
    --acr-name)                 acr_name="$2"; shift 2 ;;
    --acr-token-days)           acr_token_days="$2"; shift 2 ;;
    --apply)                    [[ "$mode" == "plan" ]] || fatal "Use only one of --apply and --remove"; mode=apply; shift ;;
    --remove)                   [[ "$mode" == "plan" ]] || fatal "Use only one of --apply and --remove"; mode=remove; shift ;;
    --config-preview)           config_preview=true; shift ;;
    *)                          fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq
if [[ -n "$acr_name" && "$mode" != "plan" ]]; then
  require_tools osmo
fi

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

[[ -n "$tenant_id" && -n "$subscription_id" ]] || fatal "--tenant-id and --subscription are required"
[[ -n "$arc_cluster_resource_id" ]] || fatal "--arc-cluster-resource-id is required"
[[ -n "$workflow_identity_id" ]] || fatal "--workflow-identity-id is required"

lowercase() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
guid_pattern='^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
[[ "$(lowercase "$tenant_id")" =~ $guid_pattern && "$(lowercase "$subscription_id")" =~ $guid_pattern ]] \
  || fatal "--tenant-id and --subscription must be GUIDs"
# Every resource must live in --subscription, because each az call below targets that subscription.
resource_pattern="^/subscriptions/$(lowercase "$subscription_id")/resourcegroups/[^/]+/providers"
[[ "$(lowercase "$arc_cluster_resource_id")" =~ ${resource_pattern}/microsoft\.kubernetes/connectedclusters/[^/]+$ ]] \
  || fatal "--arc-cluster-resource-id must identify a Microsoft.Kubernetes/connectedClusters resource in --subscription"
[[ "$(lowercase "$workflow_identity_id")" =~ ${resource_pattern}/microsoft\.managedidentity/userassignedidentities/[^/]+$ ]] \
  || fatal "--workflow-identity-id must identify a user-assigned managed identity in --subscription"
if [[ -n "$dataviewer_identity_id" ]]; then
  [[ "$(lowercase "$dataviewer_identity_id")" =~ ${resource_pattern}/microsoft\.managedidentity/userassignedidentities/[^/]+$ ]] \
    || fatal "--dataviewer-identity-id must identify a user-assigned managed identity in --subscription"
  [[ -n "$storage_account" ]] || fatal "--dataviewer-identity-id needs --storage-account"
fi
[[ -z "$storage_account" || -n "$dataviewer_identity_id" ]] || fatal "--storage-account needs --dataviewer-identity-id"
[[ "$acr_token_days" =~ ^[1-9][0-9]*$ ]] || fatal "--acr-token-days must be a positive whole number"

# Names derive from the Arc cluster, so each cluster gets its own credentials and reruns find them again.
cluster_hash=$(lowercase "$arc_cluster_resource_id" | calculate_sha256 /dev/stdin | cut -c1-16)
workflow_subject="system:serviceaccount:${LOCAL_OSMO_WORKFLOWS_NAMESPACE}:osmo-workflow"
workflow_credential="osmo-local-${cluster_hash}"
dataviewer_subject="system:serviceaccount:${LOCAL_DATAVIEWER_NAMESPACE}:dataviewer-backend"
dataviewer_credential="dataviewer-local-${cluster_hash}"
acr_token_name="osmo-local-${cluster_hash}"
osmo_registry_credential="acr-${acr_name}"
token_audience="api://AzureADTokenExchange"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Mode" "$mode"
  print_kv "Tenant" "$tenant_id"
  print_kv "Subscription" "$subscription_id"
  print_kv "Arc Cluster" "$arc_cluster_resource_id"
  print_kv "Workflow Identity" "$workflow_identity_id"
  print_kv "Workflow Credential" "$workflow_credential for $workflow_subject"
  if [[ -n "$dataviewer_identity_id" ]]; then
    print_kv "Dataviewer Identity" "$dataviewer_identity_id"
    print_kv "Dataviewer Credential" "$dataviewer_credential for $dataviewer_subject"
    print_kv "Dataviewer Storage" "$storage_account"
  else
    print_kv "Dataviewer" "not requested"
  fi
  if [[ -n "$acr_name" ]]; then
    print_kv "ACR Pull Token" "$acr_token_name on $acr_name (password valid $acr_token_days days)"
    print_kv "OSMO Credential" "$osmo_registry_credential (REGISTRY)"
  else
    print_kv "ACR" "not requested"
  fi
  print_kv "Role Changes" "none"
  exit 0
fi

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
chmod 0700 "$work_dir"

# Print the identity's name, resource group, client ID, and principal ID, separated by tabs.
read_identity() {
  az identity show --ids "$1" --output json | jq -er '[.name, .resourceGroup, .clientId, .principalId]
    | if all(type == "string" and length > 0) then @tsv else error("incomplete identity") end'
}

# Print present, missing, or mismatch for the federated credential an identity should hold.
federated_credential_state() {
  local identity_name="$1" resource_group="$2" credential="$3" subject="$4" output
  if output=$(az identity federated-credential show --identity-name "$identity_name" \
    --resource-group "$resource_group" --subscription "$subscription_id" --name "$credential" \
    --output json 2>"$work_dir/credential-show.err"); then
    if jq -e --arg issuer "$arc_issuer" --arg subject "$subject" --arg audience "$token_audience" \
      '.issuer == $issuer and .subject == $subject and .audiences == [$audience]' <<< "$output" >/dev/null; then
      echo present
    else
      echo mismatch
    fi
  elif grep -Eqi 'ResourceNotFound|NotFound|404' "$work_dir/credential-show.err"; then
    echo missing
  else
    fatal "Unable to read federated credential $credential on $identity_name"
  fi
}

# Print the identity's role names, or a note when the caller cannot list role assignments.
identity_roles() {
  local roles
  if roles=$(az role assignment list --assignee "$1" --all --query "[].roleDefinitionName" --output tsv 2>/dev/null); then
    roles=$(sort -u <<< "$roles" | paste -sd, - | sed 's/,/, /g')
    echo "${roles:-none}"
  else
    echo "unknown (cannot list role assignments)"
  fi
}

# Warn when an identity lacks a role that the access it is federated for needs.
expect_role() {
  local roles="$1" role="$2" purpose="$3"
  [[ "$roles" == unknown* || ", $roles, " == *", $role, "* ]] || warn "The identity lacks $role, which $purpose needs"
}

describe_state() {
  case "$1:$mode" in
    present:remove)  echo "present; --remove deletes it" ;;
    present:*)       echo "present" ;;
    missing:remove)  echo "missing" ;;
    missing:*)       echo "missing; --apply creates it" ;;
    mismatch:remove) echo "exists with a different issuer, subject, or audience; --remove deletes it" ;;
    mismatch:*)      echo "exists with a different issuer, subject, or audience; inspect it before applying" ;;
  esac
}

# Print present, drifted, unreadable, or missing for this cluster's ACR token. A token is present only when it
# holds this registry's built-in pull-only scope map and is enabled; anything else is drifted, so --apply never
# reissues a password for broader access. A token whose scope map or status cannot be read stops the plan and
# --apply, and is unreadable for --remove, which deletes it either way.
acr_token_state_of() {
  local output
  if ! output=$(az acr token show --name "$acr_token_name" --registry "$acr_name" --subscription "$subscription_id" \
    --query '{scopeMapId: scopeMapId, status: status}' --output json 2>"$work_dir/token-show.err"); then
    grep -Eqi 'ResourceNotFound|NotFound|404' "$work_dir/token-show.err" \
      || fatal "Unable to read ACR token $acr_token_name on $acr_name; nothing was changed"
    echo missing
    return 0
  fi
  if ! jq -e '[.scopeMapId, .status] | all(type == "string" and length > 0)' <<< "$output" >/dev/null 2>&1; then
    [[ "$mode" == "remove" ]] || fatal "Unable to read the scope map and status of ACR token $acr_token_name on $acr_name; nothing was changed"
    echo unreadable
    return 0
  fi
  if jq -e --arg scope_map "$(lowercase "$acr_id/scopeMaps/_repositories_pull")" \
    '(.scopeMapId | ascii_downcase) == $scope_map and (.status | ascii_downcase) == "enabled"' <<< "$output" >/dev/null; then
    echo present
  else
    echo drifted
  fi
}

#------------------------------------------------------------------------------
# Plan
#------------------------------------------------------------------------------
section "Azure Access Plan"

az account show --output json | jq -e --arg tenant "$(lowercase "$tenant_id")" --arg subscription "$(lowercase "$subscription_id")" '
  ((.tenantId // "") | ascii_downcase) == $tenant and ((.id // "") | ascii_downcase) == $subscription
' >/dev/null || fatal "The active Azure account does not match --tenant-id and --subscription"

arc_cluster=$(az connectedk8s show --ids "$arc_cluster_resource_id" --output json)
jq -e '.securityProfile.workloadIdentity.enabled == true and
  (.oidcIssuerProfile.issuerUrl | type == "string" and test("^https://[^[:space:]]+$"))' <<< "$arc_cluster" >/dev/null \
  || fatal "The Arc cluster must have workload identity enabled and an OIDC issuer"
arc_issuer=$(jq -r '.oidcIssuerProfile.issuerUrl' <<< "$arc_cluster")
print_kv "Arc OIDC Issuer" "$arc_issuer"

workflow_identity=$(read_identity "$workflow_identity_id")
IFS=$'\t' read -r workflow_identity_name workflow_resource_group workflow_client_id workflow_principal_id \
  <<< "$workflow_identity"
workflow_state=$(federated_credential_state "$workflow_identity_name" "$workflow_resource_group" \
  "$workflow_credential" "$workflow_subject")
workflow_roles=$(identity_roles "$workflow_principal_id")
print_kv "Workflow Identity" "$workflow_identity_name (client $workflow_client_id)"
print_kv "Workflow Roles" "$workflow_roles"
print_kv "Workflow Credential" "$workflow_credential: $(describe_state "$workflow_state")"
expect_role "$workflow_roles" "AzureML Data Scientist" "Azure ML tracking from local jobs"
expect_role "$workflow_roles" "Storage Blob Data Contributor" "Blob datasets and checkpoints from local jobs"

dataviewer_state=""
if [[ -n "$dataviewer_identity_id" ]]; then
  dataviewer_identity=$(read_identity "$dataviewer_identity_id")
  IFS=$'\t' read -r dataviewer_identity_name dataviewer_resource_group dataviewer_client_id dataviewer_principal_id \
    <<< "$dataviewer_identity"
  dataviewer_state=$(federated_credential_state "$dataviewer_identity_name" "$dataviewer_resource_group" \
    "$dataviewer_credential" "$dataviewer_subject")
  dataviewer_roles=$(identity_roles "$dataviewer_principal_id")
  print_kv "Dataviewer Identity" "$dataviewer_identity_name (client $dataviewer_client_id)"
  print_kv "Dataviewer Roles" "$dataviewer_roles"
  print_kv "Dataviewer Credential" "$dataviewer_credential: $(describe_state "$dataviewer_state")"
  expect_role "$dataviewer_roles" "Storage Blob Data Contributor" "the dataviewer's Blob datasets and annotations"
fi

acr_token_state=""
if [[ -n "$acr_name" ]]; then
  acr_registry=$(az acr show --name "$acr_name" --subscription "$subscription_id" \
    --query '{id: id, loginServer: loginServer}' --output json \
    | jq -er '[.id, .loginServer] | if all(type == "string" and length > 0) then @tsv else error("incomplete registry") end')
  IFS=$'\t' read -r acr_id acr_login_server <<< "$acr_registry"
  acr_token_state=$(acr_token_state_of)
  case "$acr_token_state:$mode" in
    present:apply)     print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: present; --apply issues a new password" ;;
    drifted:remove)    print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: not pull-only or disabled; --remove deletes it" ;;
    drifted:*)         print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: not pull-only or disabled; inspect it before applying" ;;
    unreadable:remove) print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: scope map or status unreadable; --remove deletes it" ;;
    missing:remove)    print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: missing" ;;
    missing:*)         print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: missing; --apply creates it (pull only)" ;;
    *)                 print_kv "ACR Pull Token" "$acr_token_name on $acr_login_server: present" ;;
  esac
  print_kv "OSMO Credential" "$osmo_registry_credential (REGISTRY) in the local OSMO"
fi

#------------------------------------------------------------------------------
# Apply or Remove
#------------------------------------------------------------------------------

create_federated_credential() {
  local identity_name="$1" resource_group="$2" credential="$3" subject="$4"
  az identity federated-credential create --identity-name "$identity_name" --resource-group "$resource_group" \
    --subscription "$subscription_id" --name "$credential" --issuer "$arc_issuer" --subject "$subject" \
    --audiences "$token_audience" --output none
  info "Created federated credential $credential on $identity_name"
}

delete_federated_credential() {
  local identity_name="$1" resource_group="$2" credential="$3"
  az identity federated-credential delete --identity-name "$identity_name" --resource-group "$resource_group" \
    --subscription "$subscription_id" --name "$credential" --yes --output none
  info "Deleted federated credential $credential from $identity_name"
}

if [[ "$mode" == "apply" ]]; then
  section "Apply"
  [[ "$workflow_state" != "mismatch" && "$dataviewer_state" != "mismatch" ]] \
    || fatal "A federated credential with this name has a different issuer, subject, or audience; nothing was changed"
  [[ "$acr_token_state" != "drifted" ]] \
    || fatal "ACR token $acr_token_name on $acr_name is not limited to pulls from this registry, or is disabled; nothing was changed. A password already stored in the local OSMO is unchanged and may still work: inspect the token, or run --remove to delete the token and the stored credential"
  [[ "$workflow_state" == "present" ]] \
    || create_federated_credential "$workflow_identity_name" "$workflow_resource_group" "$workflow_credential" "$workflow_subject"
  if [[ "$dataviewer_state" == "missing" ]]; then
    create_federated_credential "$dataviewer_identity_name" "$dataviewer_resource_group" \
      "$dataviewer_credential" "$dataviewer_subject"
  fi
  if [[ -n "$acr_name" ]]; then
    load_platform_profile local
    use_platform_osmo_login
    # The password exists only in this process and reaches the OSMO CLI through file descriptors.
    if [[ "$acr_token_state" == "missing" ]]; then
      acr_password=$(az acr token create --name "$acr_token_name" --registry "$acr_name" \
        --subscription "$subscription_id" --scope-map _repositories_pull \
        --expiration-in-days "$acr_token_days" --output json | jq -er '.credentials.passwords[0].value')
      info "Created pull-only ACR token $acr_token_name"
    else
      acr_password=$(az acr token credential generate --name "$acr_token_name" --registry "$acr_name" \
        --subscription "$subscription_id" --password1 --expiration-in-days "$acr_token_days" \
        --output json | jq -er '.passwords[] | select(.name == "password1") | .value')
      info "Issued a new password for ACR token $acr_token_name"
    fi
    osmo credential set "$osmo_registry_credential" --type REGISTRY --payload-file \
      registry=<(printf '%s' "$acr_login_server") \
      username=<(printf '%s' "$acr_token_name") \
      auth=<(printf '%s' "$acr_password") >/dev/null
    unset acr_password
    info "Stored REGISTRY credential $osmo_registry_credential in the local OSMO"
  fi
elif [[ "$mode" == "remove" ]]; then
  section "Remove"
  # Credential names derive from this cluster, so a drifted one is still this script's to delete.
  [[ "$workflow_state" == "missing" ]] \
    || delete_federated_credential "$workflow_identity_name" "$workflow_resource_group" "$workflow_credential"
  if [[ "$dataviewer_state" == "present" || "$dataviewer_state" == "mismatch" ]]; then
    delete_federated_credential "$dataviewer_identity_name" "$dataviewer_resource_group" "$dataviewer_credential"
  fi
  if [[ -n "$acr_name" ]]; then
    if [[ "$acr_token_state" != "missing" ]]; then
      az acr token delete --name "$acr_token_name" --registry "$acr_name" --subscription "$subscription_id" \
        --yes --output none
      info "Deleted ACR token $acr_token_name"
    fi
    load_platform_profile local
    use_platform_osmo_login
    if osmo_has_credential "$osmo_registry_credential"; then
      osmo credential delete "$osmo_registry_credential" >/dev/null
      info "Deleted REGISTRY credential $osmo_registry_credential from the local OSMO"
    fi
  fi
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Cluster Changes"

case "$mode" in
  plan)   info "Plan only: nothing changed in Azure or the cluster. Rerun with --apply to create what is missing." ;;
  apply)  info "Azure access is ready. Redeploy to let the cluster use it:" ;;
  remove) info "Azure access is removed. Redeploy without the Azure options to take the cluster off Azure:" ;;
esac
if [[ "$mode" == "remove" ]]; then
  print_kv "OSMO" "infrastructure/local/02-deploy-osmo.sh (with LOCAL_OSMO_AZURE_CLIENT_ID unset)"
  [[ -z "$dataviewer_identity_id" ]] || print_kv "Dataviewer" "infrastructure/local/04-deploy-dataviewer.sh"
else
  print_kv "OSMO" "infrastructure/local/02-deploy-osmo.sh --azure-client-id $workflow_client_id"
  if [[ -n "$dataviewer_identity_id" ]]; then
    print_kv "Dataviewer" "infrastructure/local/04-deploy-dataviewer.sh --azure-storage-account $storage_account --azure-client-id $dataviewer_client_id"
  fi
  print_kv "Azure ML Tracking" "TRACKING_BACKEND=azureml with AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME on --profile local submissions"
  [[ -z "$acr_name" ]] || print_kv "ACR Images" "reference $acr_login_server/<repository>@<digest> in workflows; OSMO pulls with $osmo_registry_credential"
fi
