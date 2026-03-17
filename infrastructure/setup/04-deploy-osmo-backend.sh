#!/usr/bin/env bash
# Deploy OSMO Backend Operator, configure backend scheduling, and workflow storage
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
# shellcheck disable=SC1091
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

VALUES_DIR="$SCRIPT_DIR/values"
CONFIG_DIR="$SCRIPT_DIR/config"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy OSMO Backend Operator and configure workflow storage.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --service-url URL       OSMO control plane URL (default: auto-detect)
    --chart-version VER     Helm chart version (default: $OSMO_CHART_VERSION)
    --image-version TAG     OSMO image tag (default: $OSMO_IMAGE_VERSION)
    --backend-name NAME     Backend identifier (default: default)
    --container-name NAME   Blob container for workflows (default: osmo)
    --use-acr               Pull images from ACR deployed by 001-iac
    --acr-name NAME         Pull images from specified ACR
    --use-access-keys       Use storage access keys instead of workload identity
    --regenerate-token      Force creation of a fresh service token
    --expires-at DATE       Token expiry date YYYY-MM-DD (default: +1 year)
    --config-preview        Print configuration and exit
    --skip-preflight        Skip preflight version checks
    --use-local-osmo        Use local osmo-dev CLI instead of production osmo
    --skip-configure-datasets  Skip dataset bucket configuration
    --dataset-container NAME   Container name for datasets (default: datasets)
    --dataset-bucket NAME      OSMO bucket name (default: training)

EXAMPLES:
    $(basename "$0") --use-acr
    $(basename "$0") --use-acr --backend-name gpu-pool --use-access-keys
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
chart_version="$OSMO_CHART_VERSION"
image_version="$OSMO_IMAGE_VERSION"
[[ "$OSMO_USE_PRERELEASE" == "true" ]] && chart_version="$OSMO_PRERELEASE_CHART_VERSION"
[[ "$OSMO_USE_PRERELEASE" == "true" ]] && image_version="$OSMO_PRERELEASE_IMAGE_VERSION"
backend_name="default"
backend_description="Default backend pool"
container_name="osmo"
service_url=""
use_acr=false
acr_name=""
use_access_keys=false
osmo_identity_client_id=""
regenerate_token=false
custom_expiry=""
config_preview=false
skip_preflight=false
use_local_osmo=false
skip_configure_datasets=false
dataset_container="${DATASET_CONTAINER_NAME}"
dataset_bucket="${DATASET_BUCKET_NAME}"
chart_version_set=false
image_version_set=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    -t|--tf-dir)           tf_dir="$2"; shift 2 ;;
    --service-url)         service_url="$2"; shift 2 ;;
    --chart-version)       chart_version="$2"; chart_version_set=true; shift 2 ;;
    --image-version)       image_version="$2"; image_version_set=true; shift 2 ;;
    --backend-name)        backend_name="$2"; shift 2 ;;
    --backend-description) backend_description="$2"; shift 2 ;;
    --container-name)      container_name="$2"; shift 2 ;;
    --use-acr)             use_acr=true; shift ;;
    --acr-name)            acr_name="$2"; use_acr=true; shift 2 ;;
    --use-access-keys)     use_access_keys=true; shift ;;
    --osmo-identity-client-id) osmo_identity_client_id="$2"; shift 2 ;;
    --regenerate-token)    regenerate_token=true; shift ;;
    --expires-at)          custom_expiry="$2"; shift 2 ;;
    --config-preview)      config_preview=true; shift ;;
    --skip-preflight)      skip_preflight=true; shift ;;
    --use-local-osmo)      use_local_osmo=true; shift ;;
    --skip-configure-datasets) skip_configure_datasets=true; shift ;;
    --dataset-container)   dataset_container="$2"; shift 2 ;;
    --dataset-bucket)      dataset_bucket="$2"; shift 2 ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools terraform osmo kubectl helm jq az envsubst

run_preflight_checks() {
  section "Preflight Version Checks"
  validate_version_pair "$chart_version" "$image_version" "$chart_version_set" "$image_version_set"
}

az account show &>/dev/null || fatal "Azure CLI not logged in; run 'az login'"

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

if [[ -z "$service_url" ]]; then
  info "Auto-detecting OSMO service URL..."
  service_url=$(detect_service_url)
  [[ -z "$service_url" ]] && fatal "Could not detect service URL. Run 03-deploy-osmo-control-plane.sh first or provide --service-url"
  info "Detected: $service_url"
fi

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

storage_name=$(tf_require "$tf_output" "storage_account.value.name" "Storage account name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
location=$(tf_require "$tf_output" "resource_group.value.location" "Location")

[[ "$use_acr" == "true" && -z "$acr_name" ]] && acr_name=$(detect_acr_name "$tf_output")
[[ "$use_access_keys" == "false" && -z "$osmo_identity_client_id" ]] && osmo_identity_client_id=$(detect_osmo_identity "$tf_output")

# Read node pool configurations from terraform state
node_pools_json=$(tf_get "$tf_output" "node_pools.value")
[[ -n "$node_pools_json" ]] || fatal "node_pools output not found in terraform state. Run 'terraform apply' in $tf_dir."

pool_ids=$(echo "$node_pools_json" | jq -r 'keys[]')
[[ -n "$pool_ids" ]] || fatal "No node pools found in terraform state"

# Auto-select default pool if not explicitly configured
if [[ -z "${DEFAULT_POOL:-}" ]]; then
  DEFAULT_POOL=$(echo "$node_pools_json" | jq -r 'keys | sort | first')
  warn "DEFAULT_POOL not set — auto-selected '$DEFAULT_POOL' (first pool alphabetically). Set DEFAULT_POOL in .env.local to control this."
fi

# Compute endpoints
acr_login_server="${acr_name}.azurecr.io"
account_fqdn="${storage_name}.blob.core.windows.net"
workflow_base_url="https://${account_fqdn}:443/${container_name}"
azure_container="azure://${storage_name}/${container_name}"

# Token expiry
if [[ -n "$custom_expiry" ]]; then
  expiry_date=$(date -u -d "$custom_expiry" +%F 2>/dev/null) || \
    expiry_date=$(date -u -j -f "%Y-%m-%d" "$custom_expiry" +%F 2>/dev/null) || \
    fatal "--expires-at must be YYYY-MM-DD format"
else
  expiry_date=$(date -u -d "+1 year" +%F 2>/dev/null) || \
    expiry_date=$(date -u -v+1y +%F 2>/dev/null) || \
    fatal "Unable to compute token expiry date"
fi

# Storage access key (only when using access-keys mode)
account_key=""
[[ "$use_access_keys" == "true" ]] && account_key=$(az storage account keys list -g "$rg" -n "$storage_name" --query '[0].value' -o tsv)

auth_mode="workload-identity"
[[ "$use_access_keys" == "true" ]] && auth_mode="access-keys"
dataset_template="$CONFIG_DIR/dataset-config-${auth_mode}.template.json"

workflow_template="$CONFIG_DIR/${WORKFLOW_TEMPLATE}"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Service URL" "$service_url"
  print_kv "Backend Name" "$backend_name"
  print_kv "Chart Version" "$chart_version"
  print_kv "Image Version" "$image_version"
  print_kv "Storage Account" "$storage_name"
  print_kv "Container" "$container_name"
  print_kv "ACR" "$([[ $use_acr == true ]] && echo "$acr_login_server" || echo 'nvcr.io')"
  print_kv "Auth Mode" "$([[ $use_access_keys == true ]] && echo 'access-keys' || echo 'workload-identity')"
  print_kv "Workflow Template" "$workflow_template"
  print_kv "Data Validation" "$([[ $use_access_keys == true ]] && echo 'enabled' || echo 'disabled (workload identity)')"
  print_kv "Token Expiry" "$expiry_date"
  print_kv "Pools" "$(echo "$pool_ids" | tr '\n' ' ')"
  print_kv "Default Pool" "default (shares with $DEFAULT_POOL)"
  print_kv "Dataset Container" "$dataset_container"
  print_kv "Dataset Bucket" "$dataset_bucket"
  exit 0
fi

#------------------------------------------------------------------------------
# Validate Required Files
#------------------------------------------------------------------------------

values_file="$VALUES_DIR/osmo-backend-operator.yaml"
identity_values="$VALUES_DIR/osmo-backend-operator-identity.yaml"
scheduler_template="$CONFIG_DIR/scheduler-config.template.json"
pod_template_file="$CONFIG_DIR/pod-template-config.template.json"
pool_template="$CONFIG_DIR/pool-config.template.json"
platform_template="$CONFIG_DIR/platform-template-config.template.json"
account_secret="osmo-operator-token"

required_files=("$values_file" "$scheduler_template" "$pod_template_file" "$workflow_template" "$pool_template" "$platform_template")
[[ "$skip_configure_datasets" == "false" ]] && required_files+=("$dataset_template")

for f in "${required_files[@]}"; do
  [[ -f "$f" ]] || fatal "Required file not found: $f"
done

mkdir -p "$CONFIG_DIR/out"

#------------------------------------------------------------------------------
# OSMO Login
#------------------------------------------------------------------------------
section "OSMO Login"
osmo_login_and_setup "$service_url"

#------------------------------------------------------------------------------
# Prepare Namespaces and Service Token
#------------------------------------------------------------------------------
section "Prepare Namespaces and Token"

ensure_namespace "$NS_OSMO_OPERATOR"
ensure_namespace "$NS_OSMO_WORKFLOWS"

token_exists=false
kubectl get secret "$account_secret" -n "$NS_OSMO_OPERATOR" &>/dev/null && token_exists=true

if [[ "$regenerate_token" == "true" || "$token_exists" == "false" ]]; then
  token_name="backend-token-$(date -u +%Y%m%d%H%M%S)"
  info "Generating OSMO service token $token_name..."

  token_json=$(osmo token set "$token_name" \
    --expires-at "$expiry_date" \
    --description "Backend Operator Token" \
    --roles osmo-backend -t json)

  OSMO_SERVICE_TOKEN=$(echo "$token_json" | jq -r '.token // empty')
  [[ -z "$OSMO_SERVICE_TOKEN" ]] && fatal "Failed to obtain service token"
  export OSMO_SERVICE_TOKEN

  kubectl create secret generic "$account_secret" \
    --namespace="$NS_OSMO_OPERATOR" \
    --from-literal=token="$OSMO_SERVICE_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
else
  info "Token secret $account_secret already exists"
fi

#------------------------------------------------------------------------------
# Configure Storage Container
#------------------------------------------------------------------------------
section "Configure Storage Container"

if az storage container show --account-name "$storage_name" --name "$container_name" --auth-mode login &>/dev/null; then
  info "Container '$container_name' already exists"
else
  info "Creating container '$container_name'..."
  az storage container create --account-name "$storage_name" --name "$container_name" --auth-mode login --public-access off >/dev/null
fi

#------------------------------------------------------------------------------
# Configure NGC Authentication (pre-release images)
#------------------------------------------------------------------------------

nvcr_auth_active=false
if [[ "$use_acr" == "false" ]] && is_prerelease_tag "$image_version"; then
  [[ -z "$NGC_API_KEY" ]] && fatal "NGC_API_KEY required for pre-release images from nvcr.io. Export NGC_API_KEY or use --use-acr."
  section "Configure NGC Authentication"
  create_nvcr_pull_secret "$NS_OSMO_OPERATOR" "$NGC_API_KEY" "$NVCR_PULL_SECRET"
  nvcr_auth_active=true
fi

#------------------------------------------------------------------------------
# Deploy Backend Operator
#------------------------------------------------------------------------------
section "Deploy Backend Operator"

if [[ "$use_acr" == "true" ]]; then
  login_acr "$acr_name"
else
  helm repo list -o json | jq -e '.[] | select(.name == "osmo")' >/dev/null 2>&1 || \
    helm repo add osmo https://helm.ngc.nvidia.com/nvidia/osmo >/dev/null
  helm repo update >/dev/null
fi

if [[ "$skip_preflight" == "true" ]]; then
  warn "Skipping preflight version checks (--skip-preflight)"
else
  run_preflight_checks
fi

helm_args=(
  --values "$values_file"
  --version "$chart_version"
  --namespace "$NS_OSMO_OPERATOR"
  --set-string "global.osmoImageTag=$image_version"
  --set-string "global.serviceUrl=$service_url"
  --set-string "global.agentNamespace=$NS_OSMO_OPERATOR"
  --set-string "global.backendNamespace=$NS_OSMO_WORKFLOWS"
  --set-string "global.backendName=$backend_name"
  --set-string "global.accountTokenSecret=$account_secret"
  --set-string "global.loginMethod=token"
)

if [[ "$use_acr" == "true" ]]; then
  helm_args+=(--set "global.osmoImageLocation=${acr_login_server}/osmo")
fi
[[ "$nvcr_auth_active" == "true" ]] && helm_args+=(--set "global.imagePullSecret=$NVCR_PULL_SECRET")

if [[ "$use_access_keys" == "false" ]]; then
  helm_args+=(-f "$identity_values" --set "serviceAccount.annotations.azure\.workload\.identity/client-id=$osmo_identity_client_id")
fi

if [[ "$use_acr" == "true" ]]; then
  helm upgrade -i osmo-operator "oci://${acr_login_server}/helm/backend-operator" "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
else
  helm upgrade -i osmo-operator osmo/backend-operator "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
fi

#------------------------------------------------------------------------------
# Configure OSMO Backend and Workflow Storage
#------------------------------------------------------------------------------
section "Configure OSMO Backend"

# Convert Kubernetes node taints to pod toleration JSON entries
taints_to_tolerations() {
  local taints_json="${1:?taints JSON array required}"
  echo "$taints_json" | jq '[.[] | split(":") as $parts |
    ($parts[0] | split("=")) as $kv |
    {
      key: $kv[0],
      effect: $parts[1]
    } + if ($kv | length) > 1 then {
      value: $kv[1],
      operator: "Equal"
    } else {
      operator: "Exists"
    } end]'
}

export WORKFLOW_SERVICE_ACCOUNT
export BACKEND_NAME="$backend_name"
export BACKEND_DESCRIPTION="$backend_description"
export K8S_NAMESPACE="$NS_OSMO_WORKFLOWS"
export CONTROL_PLANE_NAMESPACE="$NS_OSMO_CONTROL_PLANE"
export STORAGE_ACCESS_KEY_ID="osmo-control-plane-storage"
export STORAGE_ACCESS_KEY="$account_key"
export WORKFLOW_BASE_URL="$workflow_base_url"
export WORKFLOW_DATA_ENDPOINT="${azure_container}/workflows/data"
export WORKFLOW_LOG_ENDPOINT="${azure_container}/workflows/logs"
export WORKFLOW_APP_ENDPOINT="${azure_container}/apps"
export AZURE_REGION="$location"
export ACR_LOGIN_SERVER="$acr_login_server"

# Render shared pod template (substitutes WORKFLOW_SERVICE_ACCOUNT)
envsubst < "$pod_template_file" > "$CONFIG_DIR/out/pod-template-config.json"

# Generate platform-specific configs from terraform node pool state
combined_pools="{}"
for pool_id in $pool_ids; do
  info "Generating config for pool: $pool_id"
  pool_data=$(echo "$node_pools_json" | jq --arg id "$pool_id" '.[$id]')
  vm_size=$(echo "$pool_data" | jq -r '.vm_size')
  taints_json=$(echo "$pool_data" | jq '.node_taints')
  priority=$(echo "$pool_data" | jq -r '.priority')

  # Generate tolerations from node taints
  tolerations=$(taints_to_tolerations "$taints_json")

  # Build platform pod template entry from platform-template-config structure
  pod_template_key="aks_${pool_id}"
  platform_pod_entry=$(jq \
    --argjson tolerations "$tolerations" \
    --arg vm_size "$vm_size" \
    '.pod_template.spec.tolerations = $tolerations |
     .pod_template.spec.nodeSelector["node.kubernetes.io/instance-type"] = $vm_size |
     .pod_template' "$platform_template")

  # Merge platform pod template into rendered pod-template-config
  jq --arg key "$pod_template_key" --argjson entry "$platform_pod_entry" \
    '. + {($key): $entry}' "$CONFIG_DIR/out/pod-template-config.json" \
    > "$CONFIG_DIR/out/pod-template-config.json.tmp"
  mv "$CONFIG_DIR/out/pod-template-config.json.tmp" "$CONFIG_DIR/out/pod-template-config.json"

  # Build override_pod_template list (include workload_identity only when not using access keys)
  if [[ "$use_access_keys" == "false" ]]; then
    override_templates=$(jq -nc --arg key "$pod_template_key" '[($key), "workload_identity"]')
  else
    override_templates=$(jq -nc --arg key "$pod_template_key" '[($key)]')
  fi

  # Build platform pool entry from platform-template-config structure
  platform_name="${pool_id}_platform"
  platform_description="${pool_id} GPU platform (${priority} priority)"
  platform_pool_entry=$(jq \
    --arg desc "$platform_description" \
    --argjson templates "$override_templates" \
    '.pool_platform.description = $desc |
     .pool_platform.override_pod_template = $templates |
     .pool_platform' "$platform_template")

  # Render pool config from template and inject platform
  export POOL_NAME="$pool_id"
  export POOL_DESCRIPTION="${pool_id} GPU pool"
  export DEFAULT_PLATFORM="$platform_name"
  pool_config=$(envsubst < "$pool_template" | \
    jq --arg pname "$platform_name" --argjson pentry "$platform_pool_entry" \
    '.platforms[$pname] = $pentry')

  # Merge per-pool overrides if present
  override_file="$CONFIG_DIR/overrides/${pool_id}.json"
  if [[ -f "$override_file" ]]; then
    info "Applying pool overrides from $override_file"
    pool_config=$(echo "$pool_config" | jq -s '.[0] * .[1]' - "$override_file")
  fi

  # Add to combined pools
  combined_pools=$(jq --arg id "$pool_id" --argjson pool "$pool_config" \
    '. + {($id): $pool}' <<< "$combined_pools")
done

# Validate DEFAULT_POOL references a configured pool
if ! echo "$pool_ids" | grep -qx "$DEFAULT_POOL"; then
  fatal "DEFAULT_POOL='$DEFAULT_POOL' not found in terraform node pools: $(echo "$pool_ids" | tr '\n' ' ')"
fi

# Add "default" shared pool reusing platforms from DEFAULT_POOL
info "Adding shared 'default' pool (shares resources with $DEFAULT_POOL)"
target_platforms=$(jq --arg id "$DEFAULT_POOL" '.[$id].platforms' <<< "$combined_pools")
target_default_platform=$(jq -r --arg id "$DEFAULT_POOL" '.[$id].default_platform' <<< "$combined_pools")

export POOL_NAME="default"
export POOL_DESCRIPTION="Default pool (shares resources with ${DEFAULT_POOL})"
export DEFAULT_PLATFORM="$target_default_platform"
default_pool_config=$(envsubst < "$pool_template" | \
  jq --argjson platforms "$target_platforms" '.platforms = $platforms')

combined_pools=$(jq --argjson pool "$default_pool_config" \
  '. + {"default": $pool}' <<< "$combined_pools")

# Write combined pool config
jq -n --argjson pools "$combined_pools" '{"pools": $pools}' > "$CONFIG_DIR/out/combined-pool-config.json"

# Render other configs
envsubst < "$scheduler_template" > "$CONFIG_DIR/out/scheduler-config.json"
envsubst < "$workflow_template" > "$CONFIG_DIR/out/workflow-config.json"
if [[ "$use_access_keys" == "false" ]]; then
  jq '.credential_config.disable_data_validation = ["azure"]' \
    "$CONFIG_DIR/out/workflow-config.json" > "$CONFIG_DIR/out/workflow-config.json.tmp"
  mv "$CONFIG_DIR/out/workflow-config.json.tmp" "$CONFIG_DIR/out/workflow-config.json"
fi

# Apply OSMO configurations
info "Applying pod template configuration..."
osmo config update POD_TEMPLATE --file "$CONFIG_DIR/out/pod-template-config.json" --description "Pod template configuration"

info "Applying backend configuration..."
osmo config update BACKEND "$backend_name" --file "$CONFIG_DIR/out/scheduler-config.json" --description "Backend $backend_name configuration"

pool_list=$(echo "$pool_ids" | tr '\n' ', ' | sed 's/, $//')
info "Applying pool configuration (pools: ${pool_list}, default)..."
osmo config update POOL --file "$CONFIG_DIR/out/combined-pool-config.json" \
  --description "Pool configuration for ${pool_list}"

info "Applying workflow storage configuration..."
osmo config update WORKFLOW --file "$CONFIG_DIR/out/workflow-config.json" --description "Workflow storage configuration"

info "Setting default pool profile: default (shares with ${DEFAULT_POOL})..."
osmo profile set pool "default"

#------------------------------------------------------------------------------
# Configure Dataset Buckets
#------------------------------------------------------------------------------
if [[ "$skip_configure_datasets" == "false" ]]; then
  section "Configure Dataset Buckets"

  # Create dataset container
  if az storage container show --account-name "$storage_name" --name "$dataset_container" --auth-mode login &>/dev/null; then
    info "Dataset container '$dataset_container' already exists"
  else
    info "Creating dataset container '$dataset_container'..."
    az storage container create --account-name "$storage_name" --name "$dataset_container" --auth-mode login --public-access off >/dev/null
  fi

  # Export dataset variables for template rendering
  export DATASET_BUCKET_NAME="$dataset_bucket"
  export DATASET_CONTAINER_NAME="$dataset_container"
  export STORAGE_ACCOUNT_NAME="$storage_name"

  # Render and apply dataset configuration
  envsubst < "$dataset_template" > "$CONFIG_DIR/out/dataset-config.json"

  info "Applying dataset configuration..."
  osmo config update DATASET --file "$CONFIG_DIR/out/dataset-config.json" \
    --description "Dataset bucket configuration for $dataset_bucket"

  info "Verifying dataset bucket configuration..."
  osmo bucket list | grep -q "$dataset_bucket" || warn "Dataset bucket may not be configured correctly"
fi

#------------------------------------------------------------------------------
# Configure Workload Identity (if enabled)
#------------------------------------------------------------------------------
if [[ "$use_access_keys" == "false" ]]; then
  section "Configure Workload Identity"
  info "Creating workflow ServiceAccount..."
  WORKFLOWS_NAMESPACE="$NS_OSMO_WORKFLOWS" \
  OSMO_IDENTITY_CLIENT_ID="$osmo_identity_client_id" \
    envsubst < "$SCRIPT_DIR/manifests/osmo-workflow-sa.yaml" | kubectl apply -f -
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Backend Name" "$backend_name"
print_kv "Service URL" "$service_url"
print_kv "Chart Version" "$chart_version"
print_kv "Image Version" "$image_version"
print_kv "Storage Account" "$storage_name"
print_kv "Container" "$container_name"
print_kv "Agent Namespace" "$NS_OSMO_OPERATOR"
print_kv "Backend Namespace" "$NS_OSMO_WORKFLOWS"
print_kv "ACR" "$([[ $use_acr == true ]] && echo "$acr_login_server" || echo 'nvcr.io')"
print_kv "Auth Mode" "$([[ $use_access_keys == true ]] && echo 'access-keys' || echo 'workload-identity')"
print_kv "Pools" "$(echo "$pool_ids" | tr '\n' ' ') default"
print_kv "Default Pool" "default (shares with $DEFAULT_POOL)"
if [[ "$skip_configure_datasets" == "false" ]]; then
  print_kv "Dataset Bucket" "$dataset_bucket"
  print_kv "Dataset Container" "$dataset_container"
fi
echo
kubectl get pods -n "$NS_OSMO_OPERATOR" --no-headers | head -5

info "OSMO backend deployment complete"
