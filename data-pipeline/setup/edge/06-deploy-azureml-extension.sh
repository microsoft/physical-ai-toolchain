#!/usr/bin/env bash
# Install the Azure ML extension on Arc-enabled K3s and attach it as compute.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

CONFIG_TEMPLATE="$SCRIPT_DIR/config/azureml-arc-config.template.json"
DEFAULT_INSTANCE_TYPES_MANIFEST="$REPO_ROOT/infrastructure/setup/manifests/azureml-instance-types.yaml"
GENERATED_ROOT="$REPO_ROOT/infrastructure/setup/generated"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Install or update the Azure ML extension on Arc-enabled K3s and attach it to
an Azure ML workspace.

OPTIONS:
    -h, --help                         Show this help message
    --subscription-id ID              Arc cluster subscription ID (required)
    --cluster-resource-group NAME      Arc cluster resource group (required)
    --cluster-name NAME                Arc-enabled Kubernetes name (required)
    --workspace-subscription-id ID     Azure ML subscription ID (required)
    --workspace-resource-group NAME    Azure ML resource group (required)
    --workspace-name NAME              Azure ML workspace name (required)
    --extension-name NAME              Azure ML extension name
    --compute-name NAME                Attached compute name
    --identity-resource-id ID          User-assigned identity resource ID
    --connectivity-mode MODE           direct|arc-ssh (default: direct)
    --kubeconfig PATH                  Protected K3s kubeconfig (direct mode)
    --context NAME                     Explicit K3s context (direct mode)
    --arc-server-resource-group NAME   Arc server resource group (arc-ssh mode)
    --arc-server-name NAME             Arc-enabled server name (arc-ssh mode)
    --bundle-dir DIR                   Generated environment bundle (required)
    --instance-types-manifest PATH     InstanceType manifest
    --install-nvidia-device-plugin BOOL
                       Install the NVIDIA device plugin (default: true)
    --install-dcgm-exporter BOOL       Install DCGM exporter (default: true)
    --skip-instance-types              Skip applying InstanceTypes
    --config-preview                   Print configuration and exit

EXAMPLES:
    $(basename "$0") \
      --subscription-id <arc-subscription-id> \
      --cluster-resource-group <arc-resource-group> \
      --cluster-name <arc-cluster-name> \
      --workspace-subscription-id <workspace-subscription-id> \
      --workspace-resource-group <workspace-resource-group> \
      --workspace-name <workspace-name> \
      --connectivity-mode arc-ssh \
      --arc-server-resource-group <server-resource-group> \
      --arc-server-name <server-name> \
      --bundle-dir infrastructure/setup/generated/dev-001
EOF
}

run_kubectl() {
  if [[ "$connectivity_mode" == "arc-ssh" ]]; then
    az ssh arc \
      --subscription "$subscription_id" \
      --resource-group "$arc_server_resource_group" \
      --name "$arc_server_name" \
      -- sudo -n k3s kubectl "$@"
  else
    kubectl --kubeconfig "$kubeconfig" --context "$context" "$@"
  fi
}

wait_for_extension() {
  local provisioning_state=""

  for ((attempt = 1; attempt <= 30; attempt++)); do
    provisioning_state=$(az k8s-extension show \
      --name "$extension_name" \
      --cluster-type connectedClusters \
      --cluster-name "$cluster_name" \
      --resource-group "$cluster_resource_group" \
      --subscription "$subscription_id" \
      --query provisioningState \
      --output tsv 2>/dev/null || true)

    case "$provisioning_state" in
      Succeeded)
        info "Azure ML extension reached state: Succeeded"
        return 0
        ;;
      Failed)
        fatal "Azure ML extension provisioning failed"
        ;;
    esac

    info "Waiting for Azure ML extension ($attempt/30): ${provisioning_state:-pending}"
    sleep 10
  done

  fatal "Azure ML extension did not reach Succeeded within five minutes"
}

wait_for_compute() {
  local provisioning_state=""

  for ((attempt = 1; attempt <= 30; attempt++)); do
    provisioning_state=$(az ml compute show \
      --name "$compute_name" \
      --resource-group "$workspace_resource_group" \
      --workspace-name "$workspace_name" \
      --subscription "$workspace_subscription_id" \
      --query provisioning_state \
      --output tsv 2>/dev/null || true)

    case "$provisioning_state" in
      Succeeded)
        info "Azure ML compute reached state: Succeeded"
        return 0
        ;;
      Failed)
        fatal "Azure ML compute attachment failed"
        ;;
    esac

    info "Waiting for Azure ML compute ($attempt/30): ${provisioning_state:-pending}"
    sleep 10
  done

  fatal "Azure ML compute did not reach Succeeded within five minutes"
}

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
cluster_resource_group="${ARC_RESOURCE_GROUP:-}"
cluster_name="${ARC_CLUSTER_NAME:-}"
workspace_subscription_id="${AZUREML_SUBSCRIPTION_ID:-}"
workspace_resource_group="${AZUREML_RESOURCE_GROUP:-}"
workspace_name="${AZUREML_WORKSPACE_NAME:-}"
extension_name="${AZUREML_EXTENSION_NAME:-}"
compute_name="${AZUREML_COMPUTE_NAME:-}"
identity_resource_id="${AZUREML_IDENTITY_RESOURCE_ID:-}"
connectivity_mode="${AZUREML_ARC_CONNECTIVITY_MODE:-direct}"
kubeconfig="${EDGE_KUBECONFIG:-}"
context="${EDGE_K3S_CONTEXT:-}"
arc_server_resource_group="${ARC_SERVER_RESOURCE_GROUP:-}"
arc_server_name="${ARC_SERVER_NAME:-}"
bundle_dir="${ENVIRONMENT_BUNDLE_DIR:-}"
instance_types_manifest="$DEFAULT_INSTANCE_TYPES_MANIFEST"
namespace="${AZUREML_NAMESPACE:-azureml}"
enable_training="true"
enable_inference="false"
cluster_purpose="DevTest"
install_nvidia_device_plugin="${AZUREML_INSTALL_NVIDIA_DEVICE_PLUGIN:-true}"
install_dcgm_exporter="${AZUREML_INSTALL_DCGM_EXPORTER:-true}"
relay_server_enabled="true"
skip_instance_types=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                       show_help; exit 0 ;;
    --subscription-id)               subscription_id="$2"; shift 2 ;;
    --cluster-resource-group)        cluster_resource_group="$2"; shift 2 ;;
    --cluster-name)                  cluster_name="$2"; shift 2 ;;
    --workspace-subscription-id)     workspace_subscription_id="$2"; shift 2 ;;
    --workspace-resource-group)      workspace_resource_group="$2"; shift 2 ;;
    --workspace-name)                workspace_name="$2"; shift 2 ;;
    --extension-name)                extension_name="$2"; shift 2 ;;
    --compute-name)                  compute_name="$2"; shift 2 ;;
    --identity-resource-id)          identity_resource_id="$2"; shift 2 ;;
    --connectivity-mode)             connectivity_mode="$2"; shift 2 ;;
    --kubeconfig)                    kubeconfig="$2"; shift 2 ;;
    --context)                       context="$2"; shift 2 ;;
    --arc-server-resource-group)     arc_server_resource_group="$2"; shift 2 ;;
    --arc-server-name)               arc_server_name="$2"; shift 2 ;;
    --bundle-dir)                    bundle_dir="$2"; shift 2 ;;
    --instance-types-manifest)       instance_types_manifest="$2"; shift 2 ;;
    --install-nvidia-device-plugin)  install_nvidia_device_plugin="$2"; shift 2 ;;
    --install-dcgm-exporter)         install_dcgm_exporter="$2"; shift 2 ;;
    --skip-instance-types)           skip_instance_types=true; shift ;;
    --config-preview)                config_preview=true; shift ;;
    *)                               fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$cluster_resource_group" ]] || fatal "--cluster-resource-group is required"
[[ -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ -n "$workspace_subscription_id" ]] || fatal "--workspace-subscription-id is required"
[[ -n "$workspace_resource_group" ]] || fatal "--workspace-resource-group is required"
[[ -n "$workspace_name" ]] || fatal "--workspace-name is required"
[[ -n "$bundle_dir" ]] || fatal "--bundle-dir is required"
[[ "$install_nvidia_device_plugin" == "true" || "$install_nvidia_device_plugin" == "false" ]] || \
  fatal "--install-nvidia-device-plugin must be true or false"
[[ "$install_dcgm_exporter" == "true" || "$install_dcgm_exporter" == "false" ]] || \
  fatal "--install-dcgm-exporter must be true or false"
[[ "$connectivity_mode" == "direct" || "$connectivity_mode" == "arc-ssh" ]] || \
  fatal "--connectivity-mode must be direct or arc-ssh"
if [[ "$connectivity_mode" == "direct" ]]; then
  [[ -n "$kubeconfig" ]] || fatal "--kubeconfig is required in direct mode"
  [[ -n "$context" ]] || fatal "--context is required in direct mode"
else
  [[ -n "$arc_server_resource_group" ]] || \
    fatal "--arc-server-resource-group is required in arc-ssh mode"
  [[ -n "$arc_server_name" ]] || fatal "--arc-server-name is required in arc-ssh mode"
fi

if [[ "$bundle_dir" != /* ]]; then
  bundle_dir="$REPO_ROOT/$bundle_dir"
fi
[[ "$bundle_dir" == "$GENERATED_ROOT/"* ]] || \
  fatal "--bundle-dir must be under infrastructure/setup/generated"

if [[ -z "$extension_name" ]]; then
  extension_name="azureml-${cluster_name}"
  extension_name="${extension_name:0:54}"
  extension_name="${extension_name%-}"
fi
if [[ -z "$compute_name" ]]; then
  compute_name="k3s-${cluster_name}"
  compute_name="${compute_name:0:16}"
  compute_name="${compute_name%-}"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Arc Subscription" "$subscription_id"
  print_kv "Arc Resource Group" "$cluster_resource_group"
  print_kv "Arc Kubernetes" "$cluster_name"
  print_kv "Workspace Subscription" "$workspace_subscription_id"
  print_kv "Workspace Resource Group" "$workspace_resource_group"
  print_kv "Workspace" "$workspace_name"
  print_kv "Extension" "$extension_name"
  print_kv "Compute" "$compute_name"
  print_kv "Identity" "${identity_resource_id:-system assigned}"
  print_kv "Connectivity" "$connectivity_mode"
  if [[ "$connectivity_mode" == "direct" ]]; then
    print_kv "Kubeconfig" "$kubeconfig"
    print_kv "Context" "$context"
  else
    print_kv "Arc Server Resource Group" "$arc_server_resource_group"
    print_kv "Arc Server" "$arc_server_name"
  fi
  print_kv "Namespace" "$namespace"
  print_kv "Training" "$enable_training"
  print_kv "Inference" "$enable_inference"
  print_kv "Cluster Purpose" "$cluster_purpose"
  print_kv "NVIDIA Device Plugin" "$install_nvidia_device_plugin"
  print_kv "DCGM Exporter" "$install_dcgm_exporter"
  print_kv "Relay Server" "$relay_server_enabled"
  print_kv "Workspace Storage RBAC" "Storage Blob Data Contributor"
  print_kv "Instance Types" "$([[ "$skip_instance_types" == "true" ]] && echo skipped || echo "$instance_types_manifest")"
  print_kv "Generated Bundle" "$bundle_dir"
  info "Config preview mode - exiting without changes"
  exit 0
fi

require_tools az envsubst jq
require_az_extension connectedk8s
require_az_extension k8s-extension
require_az_extension ml
if [[ "$connectivity_mode" == "arc-ssh" ]]; then
  require_az_extension ssh
else
  require_tools kubectl
fi

[[ -f "$CONFIG_TEMPLATE" && ! -L "$CONFIG_TEMPLATE" ]] || \
  fatal "Config template must be a regular non-symlink file: $CONFIG_TEMPLATE"
[[ "$skip_instance_types" == "true" || (-f "$instance_types_manifest" && ! -L "$instance_types_manifest") ]] || \
  fatal "InstanceType manifest must be a regular non-symlink file: $instance_types_manifest"
if [[ "$connectivity_mode" == "direct" ]]; then
  [[ -f "$kubeconfig" && ! -L "$kubeconfig" ]] || \
    fatal "Kubeconfig must be a regular non-symlink file: $kubeconfig"
fi

arc_cluster_id=$(az connectedk8s show \
  --name "$cluster_name" \
  --resource-group "$cluster_resource_group" \
  --subscription "$subscription_id" \
  --query id \
  --output tsv)
expected_cluster_id="/subscriptions/${subscription_id}/resourceGroups/${cluster_resource_group}/providers/Microsoft.Kubernetes/connectedClusters/${cluster_name}"
[[ "${arc_cluster_id,,}" == "${expected_cluster_id,,}" ]] || \
  fatal "Resolved Arc resource does not match the requested cluster"

run_kubectl cluster-info >/dev/null

mkdir -p "$bundle_dir"
export ENABLE_TRAINING="$enable_training"
export ENABLE_INFERENCE="$enable_inference"
export CLUSTER_PURPOSE="$cluster_purpose"
export INSTALL_NVIDIA_DEVICE_PLUGIN="$install_nvidia_device_plugin"
export INSTALL_DCGM_EXPORTER="$install_dcgm_exporter"
export RELAY_SERVER_ENABLED="$relay_server_enabled"
rendered_config="$bundle_dir/azureml-arc-config.json"
envsubst < "$CONFIG_TEMPLATE" > "$rendered_config"
jq empty "$rendered_config"

section "Install Azure ML Extension"
extension_args=(
  --name "$extension_name"
  --cluster-type connectedClusters
  --cluster-name "$cluster_name"
  --resource-group "$cluster_resource_group"
  --subscription "$subscription_id"
)
extension_json=$(az k8s-extension show "${extension_args[@]}" --output json 2>/dev/null || true)
if [[ -n "$extension_json" ]]; then
  current_enable_training=$(jq -r '.configurationSettings.enableTraining // empty' <<< "$extension_json")
  current_enable_inference=$(jq -r '.configurationSettings.enableInference // empty' <<< "$extension_json")
  current_cluster_purpose=$(jq -r '.configurationSettings.clusterPurpose // empty' <<< "$extension_json")
  if [[ "${current_enable_training,,}" == "$enable_training" && \
        "${current_enable_inference,,}" == "$enable_inference" && \
        "$current_cluster_purpose" == "$cluster_purpose" ]]; then
    info "Azure ML extension $extension_name already has the required training settings"
  else
    info "Updating Azure ML extension $extension_name training settings..."
    az k8s-extension update "${extension_args[@]}" \
      --configuration-settings \
        "enableTraining=$enable_training" \
        "enableInference=$enable_inference" \
        "clusterPurpose=$cluster_purpose" \
      --yes \
      --output none
  fi
else
  info "Creating Azure ML extension $extension_name..."
  az k8s-extension create "${extension_args[@]}" \
    --extension-type Microsoft.AzureML.Kubernetes \
    --scope cluster \
    --release-namespace "$namespace" \
    --release-train stable \
    --config-file "$rendered_config" \
    --output none
fi
wait_for_extension

if [[ "$skip_instance_types" == "false" ]]; then
  section "Apply Azure ML Instance Types"
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if run_kubectl get crd instancetypes.amlarc.azureml.com >/dev/null 2>&1; then
      break
    fi
    (( attempt == 30 )) && fatal "InstanceType CRD was not available within five minutes"
    sleep 10
  done
  run_kubectl apply -f - < "$instance_types_manifest"
fi

section "Attach Azure ML Compute"
compute_json=$(az ml compute show \
  --name "$compute_name" \
  --resource-group "$workspace_resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$workspace_subscription_id" \
  --output json 2>/dev/null || true)
compute_state=$(jq -r '.provisioning_state // .provisioningState // empty' <<< "$compute_json")
compute_resource_id=$(jq -r '.resource_id // .resourceId // empty' <<< "$compute_json")

if [[ "$compute_state" == "Succeeded" ]]; then
  [[ -z "$compute_resource_id" || "${compute_resource_id,,}" == "${arc_cluster_id,,}" ]] || \
    fatal "Compute $compute_name is attached to a different Kubernetes resource"
  info "Azure ML compute $compute_name is already attached"
else
  if [[ -n "$compute_state" ]]; then
    info "Detaching incomplete Azure ML compute $compute_name..."
    az ml compute detach \
      --name "$compute_name" \
      --resource-group "$workspace_resource_group" \
      --workspace-name "$workspace_name" \
      --subscription "$workspace_subscription_id" \
      --yes \
      --output none
  fi

  attach_args=(
    --name "$compute_name"
    --type Kubernetes
    --resource-id "$arc_cluster_id"
    --namespace "$namespace"
    --resource-group "$workspace_resource_group"
    --workspace-name "$workspace_name"
    --subscription "$workspace_subscription_id"
  )
  if [[ -n "$identity_resource_id" ]]; then
    attach_args+=(--identity-type UserAssigned --user-assigned-identities "$identity_resource_id")
  else
    attach_args+=(--identity-type SystemAssigned)
  fi
  az ml compute attach "${attach_args[@]}" --output none
  wait_for_compute
fi

compute_json=$(az ml compute show \
  --name "$compute_name" \
  --resource-group "$workspace_resource_group" \
  --workspace-name "$workspace_name" \
  --subscription "$workspace_subscription_id" \
  --output json)
compute_principal_id=$(jq -r '.identity.principal_id // .identity.principalId // empty' <<< "$compute_json")
[[ -n "$compute_principal_id" ]] || fatal "Azure ML compute identity principal ID is unavailable"

workspace_storage_id=$(az ml workspace show \
  --name "$workspace_name" \
  --resource-group "$workspace_resource_group" \
  --subscription "$workspace_subscription_id" \
  --query storage_account \
  --output tsv)
[[ -n "$workspace_storage_id" ]] || fatal "Azure ML workspace storage account is unavailable"

workspace_id=$(az ml workspace show \
  --name "$workspace_name" \
  --resource-group "$workspace_resource_group" \
  --subscription "$workspace_subscription_id" \
  --query id \
  --output tsv)
[[ -n "$workspace_id" ]] || fatal "Azure ML workspace resource ID is unavailable"

section "Authorize Azure ML Compute Workspace"
workspace_role_count=$(az role assignment list \
  --subscription "$workspace_subscription_id" \
  --assignee-object-id "$compute_principal_id" \
  --scope "$workspace_id" \
  --include-inherited \
  --query "[?roleDefinitionName=='AzureML Data Scientist'] | length(@)" \
  --output tsv)
if (( workspace_role_count > 0 )); then
  info "Azure ML compute identity already has workspace access"
else
  info "Granting workspace access to the Azure ML compute identity..."
  if ! az role assignment create \
      --subscription "$workspace_subscription_id" \
      --assignee-object-id "$compute_principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "AzureML Data Scientist" \
      --scope "$workspace_id" \
      --output none; then
    fatal "Unable to grant workspace access; Owner or User Access Administrator is required"
  fi
fi

section "Authorize Azure ML Compute Storage"
storage_role_count=$(az role assignment list \
  --subscription "$workspace_subscription_id" \
  --assignee-object-id "$compute_principal_id" \
  --scope "$workspace_storage_id" \
  --include-inherited \
  --query "[?roleDefinitionName=='Storage Blob Data Contributor' || roleDefinitionName=='Storage Blob Data Owner'] | length(@)" \
  --output tsv)
if (( storage_role_count > 0 )); then
  info "Azure ML compute identity already has workspace Blob data access"
else
  info "Granting workspace Blob data access to the Azure ML compute identity..."
  if ! az role assignment create \
      --subscription "$workspace_subscription_id" \
      --assignee-object-id "$compute_principal_id" \
      --assignee-principal-type ServicePrincipal \
      --role "Storage Blob Data Contributor" \
      --scope "$workspace_storage_id" \
      --output none; then
    fatal "Unable to grant workspace Blob data access; Owner or User Access Administrator is required"
  fi
fi

jq -n \
  --arg arcClusterResourceId "$arc_cluster_id" \
  --arg extensionName "$extension_name" \
  --arg workspaceSubscriptionId "$workspace_subscription_id" \
  --arg workspaceResourceGroup "$workspace_resource_group" \
  --arg workspaceName "$workspace_name" \
  --arg computeName "$compute_name" \
  --arg computePrincipalId "$compute_principal_id" \
  --arg workspaceStorageId "$workspace_storage_id" \
  --arg namespace "$namespace" \
  '{
    arcClusterResourceId: $arcClusterResourceId,
    extensionName: $extensionName,
    workspaceSubscriptionId: $workspaceSubscriptionId,
    workspaceResourceGroup: $workspaceResourceGroup,
    workspaceName: $workspaceName,
    computeName: $computeName,
    computePrincipalId: $computePrincipalId,
    workspaceStorageId: $workspaceStorageId,
    namespace: $namespace
  }' > "$bundle_dir/azureml-arc-deployment.json"

section "Deployment Summary"
print_kv "Arc Kubernetes" "$cluster_name"
print_kv "Extension" "$extension_name"
print_kv "Workspace" "$workspace_name"
print_kv "Compute" "$compute_name"
print_kv "Compute Identity" "$compute_principal_id"
print_kv "Workspace Storage RBAC" "Storage Blob Data Contributor"
print_kv "Namespace" "$namespace"
print_kv "Connectivity" "$connectivity_mode"
print_kv "Instance Types" "$([[ "$skip_instance_types" == "true" ]] && echo skipped || echo applied)"
print_kv "Generated Bundle" "$bundle_dir"
info "Azure ML Arc deployment complete"
