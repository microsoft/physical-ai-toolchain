#!/usr/bin/env bash
# Attach an Arc-connected K3s HiL cluster to the Azure ML workspace as a training-only compute
# cspell:ignore connectedclusters pids tolower
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

CONFIG_DIR="$SCRIPT_DIR/config"
MANIFESTS_DIR="$SCRIPT_DIR/manifests"
AML_DATA_SCIENTIST_ROLE="AzureML Data Scientist"
STORAGE_BLOB_CONTRIBUTOR_ROLE="Storage Blob Data Contributor"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Attach an Arc-connected K3s HiL cluster to the Azure ML workspace as a
training-only Kubernetes compute. The script installs or reconciles a
training-only Azure ML extension, applies InstanceTypes through Arc cluster
connect, attaches the compute with a system-assigned identity, and grants that
identity access to the workspace and its storage.

The extension creates an Azure Relay namespace and hybrid connection in the Arc
cluster's resource group. Don't modify them; doing so breaks the compute.

OPTIONS:
    -h, --help                        Show this help message
    -t, --tf-dir DIR                  Terraform directory (default: $DEFAULT_TF_DIR)
    --arc-cluster-resource-id ID      Arc-enabled Kubernetes resource ID (required)
    --compute-name NAME               Azure ML compute name (default: k8s-<cluster>, 16 characters at most)
    --instance-types-manifest PATH    InstanceTypes manifest (default: manifests/azureml-instance-types-hil.yaml)
    --require-gpu                     Fail unless a node reports allocatable nvidia.com/gpu
    --enforce-resource-validation     Keep the operator's check that InstanceTypes fit a Ready node
    --proxy-port PORT                 Local port for Arc cluster connect (default: 47011)
    --config-preview                  Print configuration and exit

PREREQUISITES:
    - Contributor on the Arc cluster's resource group (the extension creates the relay)
    - Kubernetes RBAC for your identity on the HiL cluster, for example from
      data-pipeline/setup/edge/05-connect-arc-kubernetes.sh --cluster-admin-signed-in-user
    - Rights to assign roles on the workspace and its storage account

EXAMPLES:
    $(basename "$0") --arc-cluster-resource-id <arc-resource-id> --config-preview
    $(basename "$0") --arc-cluster-resource-id <arc-resource-id> --require-gpu
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
arc_cluster_resource_id=""
compute_name=""
instance_types_manifest="$MANIFESTS_DIR/azureml-instance-types-hil.yaml"
require_gpu=false
skip_resource_validation="true"
proxy_port="${ARC_PROXY_PORT:-47011}"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                       show_help; exit 0 ;;
    -t|--tf-dir)                     tf_dir="$2"; shift 2 ;;
    --arc-cluster-resource-id)       arc_cluster_resource_id="$2"; shift 2 ;;
    --compute-name)                  compute_name="$2"; shift 2 ;;
    --instance-types-manifest)       instance_types_manifest="$2"; shift 2 ;;
    --require-gpu)                   require_gpu=true; shift ;;
    --enforce-resource-validation)   skip_resource_validation="false"; shift ;;
    --proxy-port)                    proxy_port="$2"; shift 2 ;;
    --config-preview)                config_preview=true; shift ;;
    *)                               fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq kubectl terraform envsubst

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

[[ -n "$arc_cluster_resource_id" ]] || fatal "--arc-cluster-resource-id is required"
arc_cluster_resource_id_lower=$(printf '%s' "$arc_cluster_resource_id" | tr '[:upper:]' '[:lower:]')
[[ "$arc_cluster_resource_id_lower" =~ ^/subscriptions/([0-9a-f-]{36})/resourcegroups/([^/]+)/providers/microsoft.kubernetes/connectedclusters/([^/]+)$ ]] || \
  fatal "--arc-cluster-resource-id must identify a Microsoft.Kubernetes/connectedClusters resource"
arc_subscription_id="${BASH_REMATCH[1]}"
# Take names from the original ID so their casing is preserved.
arc_cluster_rg=$(awk -F/ '{print $5}' <<< "$arc_cluster_resource_id")
arc_cluster_name=$(awk -F/ '{print $9}' <<< "$arc_cluster_resource_id")

if [[ -z "$compute_name" ]]; then
  compute_name="k8s-${arc_cluster_name}"
  compute_name="${compute_name:0:16}"
  compute_name="${compute_name%-}"
fi
[[ "$compute_name" =~ ^[A-Za-z][A-Za-z0-9-]{0,14}[A-Za-z0-9]$ ]] || \
  fatal "--compute-name must be 2-16 letters, digits, or hyphens, start with a letter, and not end with a hyphen"
[[ "$proxy_port" =~ ^[0-9]+$ ]] || fatal "--proxy-port must be a number"
[[ -f "$instance_types_manifest" ]] || fatal "InstanceTypes manifest not found: $instance_types_manifest"

tf_output=$(read_terraform_outputs "$tf_dir")
workspace_rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
workspace_name=$(tf_require "$tf_output" "azureml_workspace.value.name" "Azure ML workspace name")
workspace_id=$(tf_require "$tf_output" "azureml_workspace.value.id" "Azure ML workspace ID")
workspace_subscription_id=$(awk -F/ '{print tolower($3)}' <<< "$workspace_id")
[[ "$arc_subscription_id" == "$workspace_subscription_id" ]] || \
  fatal "The Arc cluster must be in the workspace's subscription ($workspace_subscription_id)"

extension_name="azureml-${arc_cluster_name}"
config_template="$CONFIG_DIR/azureml-arc-config.template.json"
[[ -f "$config_template" ]] || fatal "Extension settings template not found: $config_template"
export SKIP_RESOURCE_VALIDATION="$skip_resource_validation"
# shellcheck disable=SC2016  # envsubst takes the variable names literally
desired_settings=$(envsubst '${SKIP_RESOURCE_VALIDATION}' < "$config_template")
jq -e 'type == "object"' <<< "$desired_settings" >/dev/null || fatal "Extension settings template isn't a JSON object"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Arc Cluster" "$arc_cluster_name"
  print_kv "Arc Resource Group" "$arc_cluster_rg"
  print_kv "Workspace" "$workspace_name"
  print_kv "Workspace Group" "$workspace_rg"
  print_kv "Compute Name" "$compute_name"
  print_kv "Extension" "$extension_name (or the cluster's existing Azure ML extension)"
  print_kv "Settings" "$(jq -r 'to_entries | map("\(.key)=\(.value)") | join(", ")' <<< "$desired_settings")"
  print_kv "Instance Types" "$instance_types_manifest"
  print_kv "Require GPU" "$require_gpu"
  print_kv "Proxy Port" "$proxy_port"
  print_kv "Compute Roles" "$AML_DATA_SCIENTIST_ROLE (workspace), $STORAGE_BLOB_CONTRIBUTOR_ROLE (workspace storage)"
  info "Config preview mode — exiting without contacting Azure or Kubernetes."
  exit 0
fi

#------------------------------------------------------------------------------
# Verify Target
#------------------------------------------------------------------------------
section "Verify Target"

active_subscription_id=$(az account show --query id -o tsv | tr '[:upper:]' '[:lower:]')
[[ "$active_subscription_id" == "$arc_subscription_id" ]] || \
  fatal "The active Azure CLI subscription doesn't match the Arc cluster's; run az account set --subscription $arc_subscription_id"

arc_cluster_json=$(az connectedk8s show --ids "$arc_cluster_resource_id" -o json)
connectivity_status=$(jq -r '.connectivityStatus // "Unknown"' <<< "$arc_cluster_json")
[[ "$connectivity_status" == "Connected" ]] || \
  fatal "Arc cluster $arc_cluster_name is $connectivity_status; bring it online before attaching"
info "Arc cluster $arc_cluster_name is Connected ($(jq -r '.distribution // "unknown"' <<< "$arc_cluster_json"))"

workspace_storage_id=$(az ml workspace show --name "$workspace_name" --resource-group "$workspace_rg" \
  --query storage_account -o tsv)
[[ -n "$workspace_storage_id" ]] || fatal "Couldn't read the storage account of workspace $workspace_name"

#------------------------------------------------------------------------------
# Open Arc Cluster Connect
#------------------------------------------------------------------------------
section "Open Arc Cluster Connect"

work_dir=$(mktemp -d)
proxy_kubeconfig="$work_dir/kubeconfig"
proxy_log="$work_dir/proxy.log"
proxy_context="arc-${arc_cluster_name}"
proxy_pid=""

# az connectedk8s proxy runs Python, which runs arcProxy. arcProxy outlives a stopped
# launcher and can take several seconds to exit, so stop every descendant by PID.
descendant_pids() {
  local child
  for child in $(pgrep -P "$1" 2>/dev/null); do
    descendant_pids "$child"
    printf '%s\n' "$child"
  done
}

stop_proxy() {
  [[ -n "$proxy_pid" ]] || return 0
  local pids pid waited=0
  pids="$(descendant_pids "$proxy_pid") $proxy_pid"
  # shellcheck disable=SC2086  # word splitting yields the PID list
  kill $pids 2>/dev/null || true
  while (( waited < 10 )); do
    for pid in $pids; do
      kill -0 "$pid" 2>/dev/null && break
      pid=""
    done
    [[ -z "$pid" ]] && break
    sleep 1
    waited=$((waited + 1))
  done
  for pid in $pids; do
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  done
  proxy_pid=""
}

cleanup() {
  stop_proxy
  rm -rf "$work_dir"
}
trap cleanup EXIT

kubectl_arc() {
  kubectl --kubeconfig "$proxy_kubeconfig" --context "$proxy_context" "$@"
}

info "Starting Arc cluster connect on port $proxy_port..."
az connectedk8s proxy --ids "$arc_cluster_resource_id" --file "$proxy_kubeconfig" \
  --kube-context "$proxy_context" --port "$proxy_port" > "$proxy_log" 2>&1 &
proxy_pid=$!

proxy_ready=false
for _ in $(seq 1 45); do
  if [[ -f "$proxy_kubeconfig" ]] && kubectl_arc get --raw=/readyz &>/dev/null; then
    proxy_ready=true
    break
  fi
  kill -0 "$proxy_pid" 2>/dev/null || break
  sleep 2
done
if [[ "$proxy_ready" != "true" ]]; then
  tail -n 20 "$proxy_log" >&2 || true
  fatal "Arc cluster connect didn't become ready. Check your Kubernetes RBAC on the cluster and that port $proxy_port is free."
fi
info "Arc cluster connect is ready"

allocatable_gpus=$(kubectl_arc get nodes -o json | \
  jq '[.items[].status.allocatable["nvidia.com/gpu"] // "0" | tonumber] | add // 0')
info "Allocatable nvidia.com/gpu across nodes: $allocatable_gpus"
if [[ "$require_gpu" == "true" && "$allocatable_gpus" -lt 1 ]]; then
  fatal "No node reports allocatable nvidia.com/gpu. Run data-pipeline/setup/hil/05-enable-k3s-gpu.sh on the host first."
fi

#------------------------------------------------------------------------------
# Install or Reconcile the Azure ML Extension
#------------------------------------------------------------------------------
section "Install or Reconcile the Azure ML Extension"

mkdir -p "$CONFIG_DIR/out"
settings_file="$CONFIG_DIR/out/azureml-arc-config-${arc_cluster_name}.json"
printf '%s\n' "$desired_settings" > "$settings_file"

existing_extension=$(az k8s-extension list --cluster-type connectedClusters \
  --cluster-name "$arc_cluster_name" --resource-group "$arc_cluster_rg" -o json | \
  jq -c '[.[] | select((.extensionType // "") | ascii_downcase == "microsoft.azureml.kubernetes")][0] // empty')

settings_updated="none"
if [[ -z "$existing_extension" ]]; then
  info "Installing Azure ML extension $extension_name..."
  az k8s-extension create \
    --name "$extension_name" \
    --extension-type Microsoft.AzureML.Kubernetes \
    --cluster-type connectedClusters \
    --cluster-name "$arc_cluster_name" \
    --resource-group "$arc_cluster_rg" \
    --scope cluster \
    --release-namespace "$NS_AZUREML" \
    --release-train stable \
    --config-file "$settings_file" \
    --output none
  extension_status="Installed"
else
  extension_name=$(jq -r '.name' <<< "$existing_extension")
  info "Azure ML extension $extension_name already installed"
  settings_patch=$(jq -c --argjson current "$(jq -c '.configurationSettings // {}' <<< "$existing_extension")" \
    'with_entries(select(.value != ($current[.key] // null)))' <<< "$desired_settings")
  if [[ "$settings_patch" == "{}" ]]; then
    info "Extension settings already match"
    extension_status="Unchanged"
  else
    patch_file="$CONFIG_DIR/out/azureml-arc-settings-update-${arc_cluster_name}.json"
    printf '%s\n' "$settings_patch" > "$patch_file"
    settings_updated=$(jq -r 'to_entries | map("\(.key)=\(.value)") | join(", ")' "$patch_file")
    info "Updating extension settings: $settings_updated"
    az k8s-extension update \
      --name "$extension_name" \
      --cluster-type connectedClusters \
      --cluster-name "$arc_cluster_name" \
      --resource-group "$arc_cluster_rg" \
      --config-file "$patch_file" \
      --yes \
      --output none
    extension_status="Updated"
  fi
fi

#------------------------------------------------------------------------------
# Apply Instance Types
#------------------------------------------------------------------------------
section "Apply Instance Types"

info "Waiting for the InstanceType CRD..."
retries=30
until kubectl_arc get crd instancetypes.amlarc.azureml.com &>/dev/null; do
  (( --retries > 0 )) || fatal "InstanceType CRD not available after 5 minutes; check the extension's pods in $NS_AZUREML"
  sleep 10
done
kubectl_arc apply -f "$instance_types_manifest"
instance_types=$(kubectl_arc get instancetypes.amlarc.azureml.com -o jsonpath='{.items[*].metadata.name}')

stop_proxy

#------------------------------------------------------------------------------
# Attach Compute Target
#------------------------------------------------------------------------------
section "Attach Compute Target"

compute_state=$(az ml compute show --name "$compute_name" --resource-group "$workspace_rg" \
  --workspace-name "$workspace_name" --query provisioning_state -o tsv 2>/dev/null || echo "NotFound")
if [[ "$compute_state" == "Succeeded" ]]; then
  info "Compute $compute_name already attached"
  compute_status="Unchanged"
else
  if [[ "$compute_state" == "Failed" ]]; then
    info "Detaching failed compute $compute_name..."
    az ml compute detach --name "$compute_name" --resource-group "$workspace_rg" \
      --workspace-name "$workspace_name" --yes
  fi
  info "Attaching $arc_cluster_name as compute $compute_name..."
  az ml compute attach \
    --resource-group "$workspace_rg" \
    --workspace-name "$workspace_name" \
    --type Kubernetes \
    --name "$compute_name" \
    --resource-id "$arc_cluster_resource_id" \
    --namespace "$NS_AZUREML" \
    --identity-type SystemAssigned \
    --output none
  compute_status="Attached"
fi

#------------------------------------------------------------------------------
# Grant Compute Identity Access
#------------------------------------------------------------------------------
section "Grant Compute Identity Access"

principal_id=$(az ml compute show --name "$compute_name" --resource-group "$workspace_rg" \
  --workspace-name "$workspace_name" --query identity.principal_id -o tsv)
[[ -n "$principal_id" ]] || fatal "Compute $compute_name has no system-assigned identity"

ensure_role_assignment() {
  local role="$1" scope="$2" existing
  existing=$(az role assignment list --assignee "$principal_id" --role "$role" --scope "$scope" \
    --query "length(@)" -o tsv)
  if [[ "$existing" != "0" ]]; then
    info "$role already granted on ${scope##*/}"
    return 0
  fi
  info "Granting $role on ${scope##*/}..."
  az role assignment create --assignee-object-id "$principal_id" --assignee-principal-type ServicePrincipal \
    --role "$role" --scope "$scope" --output none
}

ensure_role_assignment "$AML_DATA_SCIENTIST_ROLE" "$workspace_id"
ensure_role_assignment "$STORAGE_BLOB_CONTRIBUTOR_ROLE" "$workspace_storage_id"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Arc Cluster" "$arc_cluster_name"
print_kv "Extension" "$extension_name ($extension_status)"
print_kv "Settings Updated" "$settings_updated"
print_kv "Instance Types" "$instance_types"
print_kv "Allocatable GPUs" "$allocatable_gpus"
print_kv "Compute" "$compute_name ($compute_status)"
print_kv "Compute Roles" "$AML_DATA_SCIENTIST_ROLE, $STORAGE_BLOB_CONTRIBUTOR_ROLE"
print_kv "Workspace" "$workspace_name"
info "Jobs on this compute reach workspace storage over its private endpoint, so the host needs the HiL VPN with private DNS."
info "Azure ML HiL compute attach complete"
