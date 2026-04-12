#!/usr/bin/env bash
# Deploy Azure Container Storage for Arc (ACSA) resources for ROS2 recording sync
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
# shellcheck disable=SC1091
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
# shellcheck disable=SC1091
source "$SCRIPT_DIR/defaults.conf"

ARC_DIR="$SCRIPT_DIR/../arc"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Install cert-manager and ACSA on an Arc-connected edge cluster, assign Blob role,
create a Blob container, and apply ACSA PVC/subvolume manifests.

OPTIONS:
    -h, --help                      Show this help message
    -t, --tf-dir DIR                Terraform directory (default: $DEFAULT_TF_DIR)
    --cluster-name NAME             Arc-connected cluster name (or ARC_CLUSTER_NAME)
    --cluster-resource-group NAME   Resource group of Arc cluster (or ARC_RESOURCE_GROUP)
    --storage-account NAME          Storage account name override
    --storage-resource-group NAME   Storage account resource group (or STORAGE_ACCOUNT_RESOURCE_GROUP)
    --connectivity-mode MODE        direct|proxy (default: direct)
    --proxy-port PORT               Arc proxy port (default: 47011)
    --config-preview                Print configuration and exit

EXAMPLES:
    $(basename "$0") --cluster-name my-edge --cluster-resource-group rg-edge
    $(basename "$0") --connectivity-mode proxy --cluster-name my-edge --cluster-resource-group rg-edge
EOF
}

wait_for_extension_state() {
  local extension_name="${1:?extension name required}"
  local desired_state="${2:?desired state required}"
  local max_attempts="${3:-30}"
  local sleep_seconds="${4:-10}"
  local provisioning_state=""

  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    provisioning_state=$(az k8s-extension show \
      --name "$extension_name" \
      --cluster-name "$cluster_name" \
      --resource-group "$cluster_resource_group" \
      --cluster-type connectedClusters \
      --query provisioningState -o tsv 2>/dev/null || true)

    if [[ "$provisioning_state" == "$desired_state" ]]; then
      info "Extension $extension_name reached state: $desired_state"
      return 0
    fi

    if [[ "$provisioning_state" == "Failed" ]]; then
      fatal "Extension $extension_name provisioning failed"
    fi

    info "Waiting for extension $extension_name ($attempt/$max_attempts): ${provisioning_state:-pending}"
    sleep "$sleep_seconds"
  done

  fatal "Timed out waiting for extension $extension_name to reach state $desired_state"
}

start_arc_proxy() {
  local kubeconfig_file="${1:?kubeconfig file required}"
  local log_file="${2:?log file required}"

  info "Starting Arc proxy for cluster $cluster_name on port $proxy_port..."
  az connectedk8s proxy \
    --name "$cluster_name" \
    --resource-group "$cluster_resource_group" \
    --file "$kubeconfig_file" \
    --port "$proxy_port" \
    >"$log_file" 2>&1 &

  proxy_pid=$!
  export KUBECONFIG="$kubeconfig_file"

  for ((attempt = 1; attempt <= 20; attempt++)); do
    if kubectl cluster-info >/dev/null 2>&1; then
      info "Arc proxy connectivity established"
      return 0
    fi
    sleep 2
  done

  fatal "Failed to establish kubectl connectivity through Arc proxy (log: $log_file)"
}

cleanup_proxy() {
  if [[ -n "${proxy_pid:-}" ]] && kill -0 "$proxy_pid" >/dev/null 2>&1; then
    kill "$proxy_pid" >/dev/null 2>&1 || true
  fi

  if [[ -n "${proxy_kubeconfig:-}" && -f "$proxy_kubeconfig" ]]; then
    rm -f "$proxy_kubeconfig"
  fi

  if [[ -n "${proxy_log_file:-}" && -f "$proxy_log_file" ]]; then
    rm -f "$proxy_log_file"
  fi

  if [[ -n "${render_dir:-}" && -d "$render_dir" ]]; then
    rm -rf "$render_dir"
  fi

  if [[ -n "${role_assignment_error_file:-}" && -f "$role_assignment_error_file" ]]; then
    rm -f "$role_assignment_error_file"
  fi
}

install_or_update_extension() {
  local extension_name="${1:?extension name required}"
  local extension_type="${2:?extension type required}"
  local extension_version="${3:?extension version required}"
  local release_train="${4:?release train required}"
  shift 4
  local config_settings=("$@")

  local common_args=(
    --name "$extension_name"
    --cluster-name "$cluster_name"
    --resource-group "$cluster_resource_group"
    --cluster-type connectedClusters
    --version "$extension_version"
    --release-train "$release_train"
    --auto-upgrade-minor-version false
  )

  if az k8s-extension show "${common_args[@]}" >/dev/null 2>&1; then
    info "Updating extension $extension_name ($extension_type)..."
    az k8s-extension update "${common_args[@]}" \
      --configuration-settings "${config_settings[@]}" \
      --yes \
      --output none
  else
    info "Creating extension $extension_name ($extension_type)..."
    az k8s-extension create "${common_args[@]}" \
      --extension-type "$extension_type" \
      --configuration-settings "${config_settings[@]}" \
      --output none
  fi
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
cluster_name="${ARC_CLUSTER_NAME:-}"
cluster_resource_group="${ARC_RESOURCE_GROUP:-}"
storage_account_name="${STORAGE_ACCOUNT_NAME:-}"
storage_account_resource_group="${STORAGE_ACCOUNT_RESOURCE_GROUP:-}"
storage_scope=""
connectivity_mode="${ACSA_CONNECTIVITY_MODE:-direct}"
proxy_port="${ACSA_PROXY_PORT:-47011}"
config_preview=false
cert_manager_extension_name="${CERT_MANAGER_EXTENSION_NAME:-arc-cert-manager}"
cert_manager_extension_version="${CERT_MANAGER_EXTENSION_VERSION:-0.10.2}"
cert_manager_release_train="${CERT_MANAGER_RELEASE_TRAIN:-stable}"
principal_id_max_retries="${ACSA_PRINCIPAL_ID_MAX_RETRIES:-12}"
principal_id_retry_seconds="${ACSA_PRINCIPAL_ID_RETRY_SECONDS:-10}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    -t|--tf-dir)                  tf_dir="$2"; shift 2 ;;
    --cluster-name)               cluster_name="$2"; shift 2 ;;
    --cluster-resource-group)     cluster_resource_group="$2"; shift 2 ;;
    --storage-account)            storage_account_name="$2"; shift 2 ;;
    --storage-resource-group)     storage_account_resource_group="$2"; shift 2 ;;
    --connectivity-mode)          connectivity_mode="$2"; shift 2 ;;
    --proxy-port)                 proxy_port="$2"; shift 2 ;;
    --config-preview)             config_preview=true; shift ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform jq kubectl envsubst
require_az_extension k8s-extension
require_az_extension connectedk8s

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

if [[ -f "$tf_dir/terraform.tfstate" ]]; then
  info "Reading terraform outputs from $tf_dir..."
  tf_output=$(read_terraform_outputs "$tf_dir")

  if [[ -z "$cluster_resource_group" ]]; then
    cluster_resource_group=$(tf_get "$tf_output" "resource_group.value.name" "")
  fi

  if [[ -z "$storage_account_name" ]]; then
    storage_account_name=$(tf_get "$tf_output" "data_lake_storage_account.value.name" "")
    storage_scope=$(tf_get "$tf_output" "data_lake_storage_account.value.id" "")
  fi

  if [[ -z "$storage_account_name" ]]; then
    storage_account_name=$(tf_get "$tf_output" "storage_account.value.name" "")
    storage_scope=$(tf_get "$tf_output" "storage_account.value.id" "")
  fi

  if [[ -n "$storage_scope" && -z "$storage_account_resource_group" ]]; then
    storage_account_resource_group=$(echo "$storage_scope" | awk -F'/' '{for (i = 1; i <= NF; i++) if ($i == "resourceGroups") {print $(i+1); exit}}')
  fi
else
  warn "terraform.tfstate not found in $tf_dir; skipping terraform output discovery"
fi

if [[ "$connectivity_mode" != "direct" && "$connectivity_mode" != "proxy" ]]; then
  fatal "Invalid connectivity mode: $connectivity_mode (expected: direct|proxy)"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "TF Dir" "$tf_dir"
  print_kv "Cluster Name" "${cluster_name:-<required>}"
  print_kv "Cluster RG" "${cluster_resource_group:-<required>}"
  print_kv "Storage Account" "${storage_account_name:-<required>}"
  print_kv "Storage RG" "${storage_account_resource_group:-<cluster-rg>}"
  print_kv "Connectivity" "$connectivity_mode"
  print_kv "Edge Namespace" "$EDGE_NAMESPACE"
  print_kv "ACSA Extension" "$ACSA_EXTENSION_NAME@$ACSA_EXTENSION_VERSION ($ACSA_RELEASE_TRAIN)"
  print_kv "Cert Extension" "$cert_manager_extension_name@$cert_manager_extension_version ($cert_manager_release_train)"
  print_kv "PVC" "$ACSA_PVC_NAME ($ACSA_PVC_SIZE, $ACSA_STORAGE_CLASS)"
  print_kv "Subvolume" "$SUBVOLUME_NAME:$SUBVOLUME_PATH"
  print_kv "Blob Container" "$BLOB_CONTAINER_NAME"
  print_kv "Apply Ingest Policy" "$ACSA_SHOULD_APPLY_INGEST_POLICY"
  info "Config preview mode - exiting without changes"
  exit 0
fi

[[ -n "$cluster_name" ]] || fatal "Cluster name is required (--cluster-name or ARC_CLUSTER_NAME)"
[[ -n "$cluster_resource_group" ]] || fatal "Cluster resource group is required (--cluster-resource-group or ARC_RESOURCE_GROUP)"
[[ -n "$storage_account_name" ]] || fatal "Storage account name is required (--storage-account or terraform output)"

subscription_id=$(az account show --query id -o tsv)
if [[ -z "$storage_scope" ]]; then
  storage_scope="/subscriptions/${subscription_id}/resourceGroups/${storage_account_resource_group:-$cluster_resource_group}/providers/Microsoft.Storage/storageAccounts/${storage_account_name}"
fi

#------------------------------------------------------------------------------
# Prepare Cluster Connectivity
#------------------------------------------------------------------------------
section "Prepare Cluster Connectivity"

proxy_pid=""
proxy_kubeconfig=""
proxy_log_file=""
render_dir=""
role_assignment_error_file=""
trap cleanup_proxy EXIT

if [[ "$connectivity_mode" == "proxy" ]]; then
  proxy_kubeconfig=$(mktemp)
  proxy_log_file=$(mktemp)
  start_arc_proxy "$proxy_kubeconfig" "$proxy_log_file"
else
  verify_cluster_connectivity
fi

ensure_namespace "$EDGE_NAMESPACE"

#------------------------------------------------------------------------------
# Install cert-manager and ACSA Extensions
#------------------------------------------------------------------------------
section "Install cert-manager and ACSA Extensions"

install_or_update_extension \
  "$cert_manager_extension_name" \
  "microsoft.certmanagement" \
  "$cert_manager_extension_version" \
  "$cert_manager_release_train" \
  "global.telemetry.enabled=true"
wait_for_extension_state "$cert_manager_extension_name" "Succeeded"

install_or_update_extension \
  "$ACSA_EXTENSION_NAME" \
  "microsoft.arc.containerstorage" \
  "$ACSA_EXTENSION_VERSION" \
  "$ACSA_RELEASE_TRAIN" \
  "edgeStorageConfiguration.create=true" \
  "feature.diskStorageClass=$ACSA_DISK_STORAGE_CLASS"
wait_for_extension_state "$ACSA_EXTENSION_NAME" "Succeeded"

#------------------------------------------------------------------------------
# Assign Blob Role to ACSA Managed Identity
#------------------------------------------------------------------------------
section "Assign Blob Role to ACSA Managed Identity"

acsa_principal_id=""
for ((attempt = 1; attempt <= principal_id_max_retries; attempt++)); do
  acsa_principal_id=$(az k8s-extension show \
    --name "$ACSA_EXTENSION_NAME" \
    --cluster-name "$cluster_name" \
    --resource-group "$cluster_resource_group" \
    --cluster-type connectedClusters \
    --query identity.principalId -o tsv 2>/dev/null || true)

  if [[ -n "$acsa_principal_id" && "$acsa_principal_id" != "null" ]]; then
    break
  fi

  info "Waiting for ACSA principal ID ($attempt/$principal_id_max_retries)..."
  sleep "$principal_id_retry_seconds"
done

[[ -n "$acsa_principal_id" && "$acsa_principal_id" != "null" ]] || fatal "ACSA managed identity principal ID is unavailable"

role_assignment_error_file=$(mktemp)
if az role assignment create \
  --assignee-object-id "$acsa_principal_id" \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Owner" \
  --scope "$storage_scope" \
  --output none 2>"$role_assignment_error_file"; then
  info "Storage Blob Data Owner role assigned"
else
  if grep -qi "already exists" "$role_assignment_error_file"; then
    info "Storage Blob Data Owner role assignment already exists"
  else
    cat "$role_assignment_error_file" >&2
    fatal "Failed to assign Storage Blob Data Owner role"
  fi
fi

#------------------------------------------------------------------------------
# Create Container and Apply ACSA Manifests
#------------------------------------------------------------------------------
section "Create Container and Apply ACSA Manifests"

az storage container create \
  --account-name "$storage_account_name" \
  --name "$BLOB_CONTAINER_NAME" \
  --auth-mode login \
  --output none

render_dir=$(mktemp -d)

export EDGE_NAMESPACE
export ACSA_STORAGE_CLASS
export ACSA_PVC_NAME
export ACSA_PVC_SIZE
export STORAGE_ACCOUNT_NAME="$storage_account_name"
export BLOB_CONTAINER_NAME
export SUBVOLUME_NAME
export SUBVOLUME_PATH
export ACSA_INGEST_POLICY
export ACSA_INGEST_POLICY_NAME
export ACSA_INGEST_ORDER
export ACSA_INGEST_MIN_DELAY_SEC
export ACSA_EVICTION_ORDER
export ACSA_EVICTION_MIN_DELAY_SEC

envsubst < "$ARC_DIR/acsa-pvc.yaml" > "$render_dir/acsa-pvc.yaml"
envsubst < "$ARC_DIR/acsa-edge-subvolume.yaml" > "$render_dir/acsa-edge-subvolume.yaml"
kubectl apply -f "$render_dir/acsa-pvc.yaml"
kubectl apply -f "$render_dir/acsa-edge-subvolume.yaml"

if [[ "$ACSA_SHOULD_APPLY_INGEST_POLICY" == "true" ]]; then
  envsubst < "$ARC_DIR/acsa-ingest-policy.yaml" > "$render_dir/acsa-ingest-policy.yaml"
  kubectl apply -f "$render_dir/acsa-ingest-policy.yaml"
fi

kubectl -n "$EDGE_NAMESPACE" wait --for=jsonpath='{.status.phase}'=Bound "pvc/$ACSA_PVC_NAME" --timeout=180s

if kubectl -n "$EDGE_NAMESPACE" get "edgevolumes/$ACSA_PVC_NAME" >/dev/null 2>&1; then
  kubectl -n "$EDGE_NAMESPACE" wait --for=jsonpath='{.status.state}'=deployed "edgevolumes/$ACSA_PVC_NAME" --timeout=180s
else
  warn "EdgeVolume $ACSA_PVC_NAME not found yet; skipping deployed-state wait"
fi

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Cluster" "$cluster_name"
print_kv "Cluster RG" "$cluster_resource_group"
print_kv "Connectivity" "$connectivity_mode"
print_kv "Namespace" "$EDGE_NAMESPACE"
print_kv "ACSA Extension" "$ACSA_EXTENSION_NAME"
print_kv "Storage Account" "$storage_account_name"
print_kv "Blob Container" "$BLOB_CONTAINER_NAME"
print_kv "PVC" "$ACSA_PVC_NAME"
print_kv "Subvolume" "$SUBVOLUME_NAME"
print_kv "Ingest Policy" "$([[ "$ACSA_SHOULD_APPLY_INGEST_POLICY" == "true" ]] && echo "$ACSA_INGEST_POLICY_NAME" || echo "default")"

info "ACSA deployment complete"
