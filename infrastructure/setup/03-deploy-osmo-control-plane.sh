#!/usr/bin/env bash
# Deploy OSMO Control Plane components (service, router, web-ui)
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

Deploy OSMO Control Plane components to AKS.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --chart-version VER     Helm chart version (default: $OSMO_CHART_VERSION)
    --image-version TAG     OSMO image tag (default: $OSMO_IMAGE_VERSION)
    --use-acr               Pull images from ACR deployed by 001-iac
    --acr-name NAME         Pull images from specified ACR
    --use-incluster-redis   Use in-cluster Redis instead of Azure Managed Redis
    --skip-mek              Skip MEK configuration
    --force-mek             Replace existing MEK (data loss warning)
    --mek-config-file PATH  Use existing MEK config file
    --skip-service-config   Skip service_base_url configuration
    --skip-preflight        Skip preflight version checks
    --use-local-osmo        Use local osmo-dev CLI instead of production osmo
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0") --use-acr
    $(basename "$0") --use-acr --use-incluster-redis
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
chart_version="$OSMO_CHART_VERSION"
image_version="$OSMO_IMAGE_VERSION"
[[ "$OSMO_USE_PRERELEASE" == "true" ]] && chart_version="$OSMO_PRERELEASE_CHART_VERSION"
[[ "$OSMO_USE_PRERELEASE" == "true" ]] && image_version="$OSMO_PRERELEASE_IMAGE_VERSION"
use_acr=false
acr_name=""
osmo_identity_client_id=""
use_incluster_redis=false
skip_mek=false
force_mek=false
mek_config_file=""
skip_service_config=false
skip_preflight=false
use_local_osmo=false
config_preview=false
chart_version_set=false
image_version_set=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    -t|--tf-dir)           tf_dir="$2"; shift 2 ;;
    --chart-version)       chart_version="$2"; chart_version_set=true; shift 2 ;;
    --image-version)       image_version="$2"; image_version_set=true; shift 2 ;;
    --use-acr)             use_acr=true; shift ;;
    --acr-name)            acr_name="$2"; use_acr=true; shift 2 ;;
    --osmo-identity-client-id) osmo_identity_client_id="$2"; shift 2 ;;
    --use-incluster-redis) use_incluster_redis=true; shift ;;
    --skip-mek)            skip_mek=true; shift ;;
    --force-mek)           force_mek=true; shift ;;
    --mek-config-file)     mek_config_file="$2"; shift 2 ;;
    --skip-service-config) skip_service_config=true; shift ;;
    --skip-preflight)      skip_preflight=true; shift ;;
    --use-local-osmo)      use_local_osmo=true; shift ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools az terraform kubectl helm jq openssl envsubst

run_preflight_checks() {
  section "Preflight Version Checks"
  validate_version_pair "$chart_version" "$image_version" "$chart_version_set" "$image_version_set"
}

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
pg_fqdn=$(tf_require "$tf_output" "postgresql_connection_info.value.fqdn" "PostgreSQL FQDN")
pg_user=$(tf_require "$tf_output" "postgresql_connection_info.value.admin_username" "PostgreSQL user")
keyvault=$(tf_require "$tf_output" "key_vault_name.value" "Key Vault name")
redis_hostname=$(tf_get "$tf_output" "managed_redis_connection_info.value.hostname")
redis_port=$(tf_get "$tf_output" "managed_redis_connection_info.value.port" "6380")

[[ "$use_acr" == "true" && -z "$acr_name" ]] && acr_name=$(detect_acr_name "$tf_output")
[[ -z "$osmo_identity_client_id" ]] && osmo_identity_client_id=$(detect_osmo_identity "$tf_output" 2>/dev/null || true)
[[ "$use_incluster_redis" == "false" && -z "$redis_hostname" ]] && fatal "Redis not deployed. Use --use-incluster-redis or ensure should_deploy_redis is true."

# Get tenant ID for workload identity authentication
tenant_id=""
[[ -n "$osmo_identity_client_id" ]] && tenant_id=$(az account show --query tenantId -o tsv)

acr_login_server="${acr_name}.azurecr.io"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  print_kv "Resource Group" "$rg"
  print_kv "Chart Version" "$chart_version"
  print_kv "Image Version" "$image_version"
  print_kv "PostgreSQL" "$pg_fqdn"
  print_kv "Redis" "$([[ $use_incluster_redis == true ]] && echo 'in-cluster' || echo "$redis_hostname:$redis_port")"
  print_kv "ACR" "$([[ $use_acr == true ]] && echo "$acr_login_server" || echo 'nvcr.io')"
  print_kv "Auth Mode" "$([[ -n $osmo_identity_client_id ]] && echo 'workload-identity' || echo 'kubectl-secrets')"
  exit 0
fi

#------------------------------------------------------------------------------
# Validate Required Files
#------------------------------------------------------------------------------

service_values="$VALUES_DIR/osmo-control-plane.yaml"
router_values="$VALUES_DIR/osmo-router.yaml"
ui_values="$VALUES_DIR/osmo-ui.yaml"
service_identity_values="$VALUES_DIR/osmo-control-plane-identity.yaml"
router_identity_values="$VALUES_DIR/osmo-router-identity.yaml"
service_config_template="$CONFIG_DIR/service-config.template.json"

for f in "$service_values" "$router_values" "$ui_values"; do
  [[ -f "$f" ]] || fatal "Values file not found: $f"
done

mkdir -p "$CONFIG_DIR/out"

#------------------------------------------------------------------------------
# Connect and Prepare Cluster
#------------------------------------------------------------------------------
section "Connect and Prepare Cluster"

connect_aks "$rg" "$cluster"
ensure_namespace "$NS_OSMO_CONTROL_PLANE"

#------------------------------------------------------------------------------
# Configure MEK (Master Encryption Key)
#------------------------------------------------------------------------------

generate_mek_config() {
  local key jwk encoded
  key="$(openssl rand -base64 32 | tr -d '\n')"
  jwk="{\"k\":\"${key}\",\"kid\":\"key1\",\"kty\":\"oct\"}"
  encoded="$(echo -n "$jwk" | base64 | tr -d '\n')"
  cat <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: $SECRET_MEK
data:
  mek.yaml: |
    currentMek: key1
    meks:
      key1: ${encoded}
EOF
}

if [[ "$skip_mek" == "false" ]]; then
  section "Configure MEK"
  mek_exists=false
  kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null && mek_exists=true

  if [[ "$mek_exists" == "true" && "$force_mek" == "false" ]]; then
    info "MEK ConfigMap already exists; skipping (use --force-mek to replace)"
  elif [[ -n "$mek_config_file" ]]; then
    [[ -f "$mek_config_file" ]] || fatal "MEK config file not found: $mek_config_file"
    info "Applying MEK from $mek_config_file..."
    kubectl apply -f "$mek_config_file" -n "$NS_OSMO_CONTROL_PLANE"
  else
    [[ "$mek_exists" == "true" ]] && warn "Replacing existing MEK - encrypted data will be unrecoverable!"
    info "Generating and applying MEK ConfigMap..."
    generate_mek_config | kubectl apply -n "$NS_OSMO_CONTROL_PLANE" -f -
    warn "Back up MEK for production: kubectl get configmap $SECRET_MEK -n $NS_OSMO_CONTROL_PLANE -o yaml > mek-backup.yaml"
  fi
fi

#------------------------------------------------------------------------------
# Configure Registry and Secrets
#------------------------------------------------------------------------------
section "Configure Registry and Secrets"

if [[ "$use_acr" == "true" ]]; then
  login_acr "$acr_name"
fi

if [[ -n "$osmo_identity_client_id" ]]; then
  info "Applying SecretProviderClass for Azure Key Vault CSI driver..."
  apply_secret_provider_class "$NS_OSMO_CONTROL_PLANE" "$keyvault" "$osmo_identity_client_id" "$tenant_id"
else
  info "Workload identity not configured; retrieving secrets from Key Vault..."
  pg_password=$(az keyvault secret show --vault-name "$keyvault" --name "psql-admin-password" --query value -o tsv)

  info "Creating database secret..."
  kubectl create secret generic "$SECRET_POSTGRES" \
    --namespace="$NS_OSMO_CONTROL_PLANE" \
    --from-literal=db-password="$pg_password" \
    --dry-run=client -o yaml | kubectl apply -f -

  if [[ "$use_incluster_redis" == "false" ]]; then
    redis_key=$(az keyvault secret show --vault-name "$keyvault" --name "redis-primary-key" --query value -o tsv)
    info "Creating Redis secret..."
    kubectl create secret generic "$SECRET_REDIS" \
      --namespace="$NS_OSMO_CONTROL_PLANE" \
      --from-literal=redis-password="$redis_key" \
      --dry-run=client -o yaml | kubectl apply -f -
  fi
fi

# Apply internal LB ingress if present
ingress_manifest="$SCRIPT_DIR/manifests/internal-lb-ingress.yaml"
[[ -f "$ingress_manifest" ]] && kubectl apply -f "$ingress_manifest"

#------------------------------------------------------------------------------
# Configure NGC Authentication (pre-release images)
#------------------------------------------------------------------------------

nvcr_auth_active=false
if [[ "$use_acr" == "false" ]] && is_prerelease_tag "$image_version"; then
  [[ -z "$NGC_API_KEY" ]] && fatal "NGC_API_KEY required for pre-release images from nvcr.io. Export NGC_API_KEY or use --use-acr."
  section "Configure NGC Authentication"
  create_nvcr_pull_secret "$NS_OSMO_CONTROL_PLANE" "$NGC_API_KEY" "$NVCR_PULL_SECRET"
  nvcr_auth_active=true
fi

#------------------------------------------------------------------------------
# Configure Helm Repository
#------------------------------------------------------------------------------

if [[ "$use_acr" == "false" ]]; then
  section "Configure Helm Repository"
  info "Adding OSMO Helm repository..."
  helm repo add osmo "$HELM_REPO_OSMO" 2>/dev/null || true
  helm repo update osmo
fi

if [[ "$skip_preflight" == "true" ]]; then
  warn "Skipping preflight version checks (--skip-preflight)"
else
  run_preflight_checks
fi

#------------------------------------------------------------------------------
# Deploy OSMO Charts
#------------------------------------------------------------------------------
section "Deploy OSMO Charts"

# Build common helm args
base_helm_args=(
  --version "$chart_version"
  --namespace "$NS_OSMO_CONTROL_PLANE"
  --set-string "global.osmoImageTag=$image_version"
)
[[ "$use_acr" == "true" ]] && base_helm_args+=(--set "global.osmoImageLocation=${acr_login_server}/osmo")
[[ "$nvcr_auth_active" == "true" ]] && base_helm_args+=(--set "global.imagePullSecret=$NVCR_PULL_SECRET")

# Deploy service
info "Deploying osmo/service..."
helm_args=("${base_helm_args[@]}" -f "$service_values" --set "services.postgres.serviceName=$pg_fqdn" --set "services.postgres.user=$pg_user")
[[ "$use_incluster_redis" == "false" ]] && helm_args+=(--set "services.redis.serviceName=$redis_hostname" --set "services.redis.port=$redis_port")
[[ -n "$osmo_identity_client_id" ]] && helm_args+=(-f "$service_identity_values" --set "serviceAccount.annotations.azure\.workload\.identity/client-id=$osmo_identity_client_id")

if [[ "$use_acr" == "true" ]]; then
  helm upgrade -i service "oci://${acr_login_server}/helm/service" "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
else
  helm upgrade -i service osmo/service "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
fi

# Deploy router
info "Deploying osmo/router..."
helm_args=("${base_helm_args[@]}" -f "$router_values" --set "services.postgres.serviceName=$pg_fqdn" --set "services.postgres.user=$pg_user")
[[ -n "$osmo_identity_client_id" ]] && helm_args+=(-f "$router_identity_values" --set "serviceAccount.annotations.azure\.workload\.identity/client-id=$osmo_identity_client_id")

if [[ "$use_acr" == "true" ]]; then
  helm upgrade -i router "oci://${acr_login_server}/helm/router" "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
else
  helm upgrade -i router osmo/router "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
fi

# Deploy web-ui
info "Deploying osmo/web-ui..."
helm_args=("${base_helm_args[@]}" -f "$ui_values" --set "services.ui.apiHostname=osmo-service.${NS_OSMO_CONTROL_PLANE}.svc.cluster.local:80")

if [[ "$use_acr" == "true" ]]; then
  helm upgrade -i ui "oci://${acr_login_server}/helm/web-ui" "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
else
  helm upgrade -i ui osmo/web-ui "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"
fi

#------------------------------------------------------------------------------
# Configure OSMO Service URL
#------------------------------------------------------------------------------

if [[ "$skip_service_config" == "false" ]]; then
  section "Configure OSMO Service"
  kubectl wait --for=condition=available deployment/osmo-service -n "$NS_OSMO_CONTROL_PLANE" --timeout=120s

  service_url=$(detect_service_url)
  if [[ -n "$service_url" ]]; then
    [[ -f "$service_config_template" ]] || fatal "Service config template not found: $service_config_template"
    export SERVICE_BASE_URL="$service_url"
    envsubst < "$service_config_template" > "$CONFIG_DIR/out/service-config.json"
    osmo_login_and_setup "$service_url"
    info "Applying service configuration (service_base_url: $service_url)..."
    osmo config update SERVICE --file "$CONFIG_DIR/out/service-config.json" --description "Set service base URL for UI"
  else
    warn "Could not determine service base URL - OSMO UI may show errors"
  fi
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Namespace" "$NS_OSMO_CONTROL_PLANE"
print_kv "Chart Version" "$chart_version"
print_kv "Image Version" "$image_version"
print_kv "Registry" "$([[ $use_acr == true ]] && echo "$acr_login_server" || echo 'nvcr.io')"
print_kv "PostgreSQL" "$pg_fqdn"
print_kv "Redis" "$([[ $use_incluster_redis == true ]] && echo 'in-cluster' || echo "$redis_hostname:$redis_port")"
print_kv "Auth Mode" "$([[ -n $osmo_identity_client_id ]] && echo 'workload-identity' || echo 'kubectl-secrets')"
echo
kubectl get pods -n "$NS_OSMO_CONTROL_PLANE" --no-headers | head -5
echo
helm list -n "$NS_OSMO_CONTROL_PLANE"

info "OSMO Control Plane deployment complete"
