#!/usr/bin/env bash
# Deploy OSMO 6.3 control plane and backend operator
#
# Prerequisites:
#   - AKS cluster deployed (infrastructure/terraform/)
#   - Terraform outputs available in tf_dir
#   - helm, kubectl, az, jq, curl, osmo, openssl, envsubst on PATH
#   - Helm repos configured (osmo repo added)
#
# Environment Variables (override via .env.local or defaults.conf):
#   OSMO_CHART_VERSION, OSMO_IMAGE_VERSION — chart and image versions
#   OSMO_USE_PRERELEASE — use prerelease versions when "true"
#   NS_OSMO_CONTROL_PLANE, NS_OSMO_OPERATOR — target namespaces
#
# Usage:
#   ./03-deploy-osmo.sh
#   ./03-deploy-osmo.sh --use-acr --acr-name myacr
#   ./03-deploy-osmo.sh --skip-backend --use-incluster-redis
#   ./03-deploy-osmo.sh --config-preview
set -o errexit -o nounset -o pipefail

_WORK_DIR=$(mktemp -d)
trap 'rm -rf "$_WORK_DIR"' EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

VALUES_DIR="$SCRIPT_DIR/values"
MANIFESTS_DIR="$SCRIPT_DIR/manifests"

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy OSMO 6.3 control plane and backend operator.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --chart-version VER     Helm chart version (default: $OSMO_CHART_VERSION)
    --image-version TAG     OSMO image tag (default: $OSMO_IMAGE_VERSION)
    --use-acr               Pull images from ACR deployed by Terraform
    --acr-name NAME         Pull images from specified ACR
    --skip-backend          Skip backend operator deployment
    --use-incluster-redis   Use in-cluster Redis instead of Azure Managed Redis
                            (unauthenticated, non-TLS; suitable for development)
    --skip-mek              Skip MEK configuration
    --force-mek             Replace existing MEK (data loss warning)
    --mek-config-file PATH  Use existing MEK config file
    --service-url URL       OSMO control plane URL (default: auto-detect)
    --backend-name NAME     Backend identifier (default: default)
    --container-name NAME   Blob container name (default: osmo)
    --skip-preflight        Skip preflight version checks
    --use-local-osmo        Use local osmo-dev CLI instead of production osmo
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --use-acr
    $(basename "$0") --skip-backend
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
skip_backend=false
use_incluster_redis=false
skip_mek=false
force_mek=false
mek_config_file=""
service_url=""
skip_preflight=false
use_local_osmo=false
config_preview=false
chart_version_set=false
image_version_set=false
backend_name="default"
container="${OSMO_WORKFLOW_BUCKET:-osmo}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)             show_help; exit 0 ;;
        -t|--tf-dir)           tf_dir="$2"; shift 2 ;;
        --chart-version)       chart_version="$2"; chart_version_set=true; shift 2 ;;
        --image-version)       image_version="$2"; image_version_set=true; shift 2 ;;
        --use-acr)             use_acr=true; shift ;;
        --acr-name)            acr_name="$2"; use_acr=true; shift 2 ;;
        --skip-backend)        skip_backend=true; shift ;;
        --use-incluster-redis) use_incluster_redis=true; shift ;;
        --skip-mek)            skip_mek=true; shift ;;
        --force-mek)           force_mek=true; shift ;;
        --mek-config-file)     mek_config_file="$2"; shift 2 ;;
        --service-url)         service_url="$2"; shift 2 ;;
        --backend-name)        backend_name="$2"; shift 2 ;;
        --container-name)      container="$2"; shift 2 ;;
        --skip-preflight)      skip_preflight=true; shift ;;
        --use-local-osmo)      use_local_osmo=true; shift ;;
        --config-preview)      config_preview=true; shift ;;
        *)                     fatal "Unknown option: $1" ;;
    esac
done

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools az terraform kubectl helm jq curl osmo openssl envsubst

if [[ "$skip_preflight" == "true" ]]; then
    warn "Skipping preflight version checks (--skip-preflight)"
else
    az account show &>/dev/null || fatal "Azure CLI not logged in; run 'az login'"
    validate_version_pair "$chart_version" "$image_version" "$chart_version_set" "$image_version_set"
fi

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

section "Read Terraform Outputs"
tf_output=$(read_terraform_outputs "$tf_dir")
storage_account=$(tf_require "$tf_output" "storage_account.value.name" "Storage account")
osmo_identity_client_id=$(tf_require "$tf_output" "osmo_workload_identity.value.client_id" "OSMO identity")
resource_group=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
aks_cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster")
pg_fqdn=$(tf_require "$tf_output" "postgresql_connection_info.value.fqdn" "PostgreSQL FQDN")
pg_user=$(tf_require "$tf_output" "postgresql_connection_info.value.admin_username" "PostgreSQL user")
kv_name=$(tf_require "$tf_output" "key_vault_name.value" "Key Vault name")
redis_hostname=$(tf_get "$tf_output" "managed_redis_connection_info.value.hostname" "")
redis_port=$(tf_get "$tf_output" "managed_redis_connection_info.value.port" "10000")
[[ "$use_incluster_redis" == "true" ]] && redis_hostname=""
if [[ -z "$redis_hostname" && "$use_incluster_redis" != "true" ]]; then
    fatal "Managed Redis not found in Terraform outputs. Use --use-incluster-redis for non-production deployments."
fi
endpoint="azure://${storage_account}/${container}"
workflow_base_url="https://${storage_account}.blob.core.windows.net:443/${container}"

service_values="$VALUES_DIR/osmo-control-plane.yaml"
service_identity_values="$VALUES_DIR/osmo-control-plane-identity.yaml"
platform_values="$VALUES_DIR/osmo-platforms.yaml"
backend_values="$VALUES_DIR/osmo-backend-operator.yaml"
backend_identity_values="$VALUES_DIR/osmo-backend-operator-identity.yaml"
workflow_sa_manifest="$MANIFESTS_DIR/osmo-workflow-sa.yaml"
internal_lb_manifest="$MANIFESTS_DIR/internal-lb-ingress.yaml"

required_files=("$service_values" "$service_identity_values" "$platform_values" "$internal_lb_manifest")
[[ "$skip_backend" == "false" ]] && required_files+=("$backend_values" "$backend_identity_values" "$workflow_sa_manifest")
for file in "${required_files[@]}"; do
    [[ -f "$file" ]] || fatal "Required file not found: $file"
done

acr_login_server=""
osmo_image_location="nvcr.io/nvidia/osmo"
service_chart_ref="osmo/${OSMO_SERVICE_CHART}"
backend_chart_ref="osmo/${OSMO_BACKEND_CHART}"
if [[ "$use_acr" == "true" ]]; then
    [[ -z "$acr_name" ]] && acr_name=$(detect_acr_name "$tf_output")
    acr_login_server="${acr_name}.azurecr.io"
    osmo_image_location="${acr_login_server}/osmo"
    service_chart_ref="oci://${acr_login_server}/helm/${OSMO_SERVICE_CHART}"
    backend_chart_ref="oci://${acr_login_server}/helm/${OSMO_BACKEND_CHART}"
fi

if [[ "$config_preview" == "true" ]]; then
    section "Configuration Preview"
    print_kv "Cluster" "$aks_cluster"
    print_kv "Resource Group" "$resource_group"
    print_kv "Service Chart" "$chart_version"
    print_kv "Image Version" "$image_version"
    print_kv "Storage Endpoint" "$endpoint"
    print_kv "Container" "$container"
    print_kv "PostgreSQL" "$pg_fqdn"
    print_kv "Redis" "${redis_hostname:-in-cluster}"
    print_kv "Registry" "$([[ $use_acr == true ]] && echo "$acr_login_server" || echo 'nvcr.io')"
    print_kv "Auth Mode" "workload-identity"
    print_kv "Backend Name" "$backend_name"
    print_kv "Backend" "$([[ $skip_backend == true ]] && echo 'skipped' || echo 'deployed')"
    print_kv "MEK" "$([[ $skip_mek == true ]] && echo 'skipped' || echo 'configured')"
    exit 0
fi

connect_aks "$resource_group" "$aks_cluster"

#------------------------------------------------------------------------------
# Phase 1a: Configure Internal LoadBalancer
#------------------------------------------------------------------------------
# Provides a stable VNet-internal IP for OSMO control-plane access (CLI/VPN).
# detect_service_url and the docs' `osmo login http://<lb-ip>` rely on it.
# Applied early so the async LB IP provisions before the post-deploy smoke test.

section "Configure Internal LoadBalancer"
info "Applying internal LoadBalancer ingress service..."
kubectl apply -f "$internal_lb_manifest"

#------------------------------------------------------------------------------
# Phase 1b: Configure Storage
#------------------------------------------------------------------------------

section "Configure Storage"
if az storage container show --account-name "$storage_account" --name "$container" --auth-mode login &>/dev/null; then
    info "Container '$container' already exists"
else
    info "Creating container '$container'..."
    az storage container create \
        --account-name "$storage_account" \
        --name "$container" \
        --auth-mode login \
        --public-access off >/dev/null
fi

#------------------------------------------------------------------------------
# Phase 1c: Configure Secrets
#------------------------------------------------------------------------------

section "Configure Secrets"
ensure_namespace "$NS_OSMO_CONTROL_PLANE"

# Create service accounts required by the chart (router and UI use separate SAs)
kubectl create sa router -n "$NS_OSMO_CONTROL_PLANE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl annotate sa router -n "$NS_OSMO_CONTROL_PLANE" "azure.workload.identity/client-id=$osmo_identity_client_id" --overwrite >/dev/null

tenant_id=$(az account show --query tenantId -o tsv)

# Admin password (needed for osmo login token — K8s secret, not CSI-mounted)
admin_password=$(az keyvault secret show --vault-name "$kv_name" --name osmo-admin-password --query value -o tsv)
kubectl create secret generic osmo-default-admin -n "$NS_OSMO_CONTROL_PLANE" \
    --from-file=password=<(printf '%s' "$admin_password") \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Pre-create DB and Redis secrets so pods can start immediately.
# CSI Secrets Store will take over rotation once pods mount the CSI volume.
pg_password=$(az keyvault secret show --vault-name "$kv_name" --name psql-admin-password --query value -o tsv)
kubectl create secret generic db-secret -n "$NS_OSMO_CONTROL_PLANE" \
    --from-literal=db-password="$pg_password" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

include_redis_secret=true
if [[ "$use_incluster_redis" == "true" ]]; then
    include_redis_secret=false
    warn "In-cluster Redis runs without TLS or authentication. Use only for development."
    kubectl create secret generic redis-secret -n "$NS_OSMO_CONTROL_PLANE" \
        --from-literal=redis-password="" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
else
    redis_key=$(az keyvault secret show --vault-name "$kv_name" --name redis-primary-key --query value -o tsv)
    kubectl create secret generic redis-secret -n "$NS_OSMO_CONTROL_PLANE" \
        --from-literal=redis-password="$redis_key" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
fi

# CSI Secrets Store: keeps secrets in sync with Key Vault after initial creation
apply_secret_provider_class "$NS_OSMO_CONTROL_PLANE" "$kv_name" "$osmo_identity_client_id" "$tenant_id" "$include_redis_secret"

#------------------------------------------------------------------------------
# Phase 1d: Configure MEK (Master Encryption Key)
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

        # Clear service_auth from DB — it was encrypted with the old MEK and is now
        # undecryptable. The service regenerates a fresh keypair on next start.
        info "Clearing stale service_auth from database..."
        # Pass the password via PGPASSWORD env, not the psql connection string:
        # kubectl stores command args in the pod spec (etcd + audit log), but not env values.
        kubectl run osmo-clear-auth --rm -i --restart=Never -n "$NS_OSMO_CONTROL_PLANE" \
            --image=postgres:16 \
            --env="PGPASSWORD=${pg_password}" \
            -- psql "host=${pg_fqdn} port=5432 dbname=osmo user=${pg_user} sslmode=require" \
            -c "DELETE FROM configs WHERE key='service_auth' AND type='SERVICE'" 2>/dev/null || \
            warn "Could not clear service_auth (DB may not be initialized yet — safe on first deploy)"

        # Ensure the service pod restarts to pick up the new MEK and regenerate service_auth.
        # Helm upgrade may not trigger a rollout if values are unchanged.
        kubectl rollout restart deployment/osmo-service -n "$NS_OSMO_CONTROL_PLANE" 2>/dev/null || true
    fi
fi

#------------------------------------------------------------------------------
# Phase 2: Deploy OSMO Service
#------------------------------------------------------------------------------

section "Deploy OSMO Service"
if [[ "$use_acr" == "true" ]]; then
    login_acr "$acr_name"
else
    helm repo add osmo "$HELM_REPO_OSMO" >/dev/null 2>&1 || true
    helm repo update osmo >/dev/null
fi

if [[ "$use_acr" == "false" ]] && is_prerelease_tag "$image_version"; then
    [[ -n "$NGC_API_KEY" ]] || fatal "NGC_API_KEY required for prerelease images from nvcr.io. Export NGC_API_KEY or use --use-acr."
    create_nvcr_pull_secret "$NS_OSMO_CONTROL_PLANE" "$NGC_API_KEY" "$NVCR_PULL_SECRET"
fi

service_helm_args=(
    --version "$chart_version"
    --namespace "$NS_OSMO_CONTROL_PLANE"
    --rollback-on-failure
    --timeout "$TIMEOUT_DEPLOY"
    --force-conflicts
    -f "$service_values"
    -f "$service_identity_values"
    -f "$platform_values"
    --set-string "global.osmoImageTag=$image_version"
    --set-string "serviceAccount.annotations.azure\.workload\.identity/client-id=$osmo_identity_client_id"
    --set-string "services.router.serviceAccount.annotations.azure\.workload\.identity/client-id=$osmo_identity_client_id"
    --set-string "services.configs.workflow.workflow_data.credential.endpoint=${endpoint}/workflows/data"
    --set-string "services.configs.workflow.workflow_data.base_url=$workflow_base_url"
    --set-string "services.configs.workflow.workflow_log.credential.endpoint=${endpoint}/workflows/logs"
    --set-string "services.configs.workflow.workflow_app.credential.endpoint=${endpoint}/apps"
    --set-string "services.postgres.serviceName=$pg_fqdn"
    --set-string "services.postgres.user=$pg_user"
    --set-string "services.configs.workflow.backend_images.init=${osmo_image_location}/init-container:${image_version}"
    --set-string "services.configs.workflow.backend_images.client=${osmo_image_location}/client:${image_version}"
)

if [[ "$use_acr" == "true" ]]; then
    service_helm_args+=(--set-string "global.osmoImageLocation=${acr_login_server}/osmo")
elif is_prerelease_tag "$image_version"; then
    service_helm_args+=(--set-string "global.imagePullSecret=$NVCR_PULL_SECRET")
fi

if [[ -n "$redis_hostname" ]]; then
    service_helm_args+=(
        --set-string "services.redis.serviceName=$redis_hostname"
        --set-string "services.redis.port=$redis_port"
    )
else
    service_helm_args+=(
        --set "services.redis.enabled=true"
        --set "services.redis.tlsEnabled=false"
        --set "services.redis.storageClassName=default"
        --set "services.redis.storageSize=1Gi"
    )
fi

if [[ "$use_acr" == "false" && -n "${OSMO_SERVICE_CHART_SHA256:-}" ]]; then
    service_chart_ref=$(pull_and_verify_chart "$service_chart_ref" "$chart_version" "$OSMO_SERVICE_CHART_SHA256" "$_WORK_DIR/service-chart")
fi

helm upgrade --install osmo "$service_chart_ref" "${service_helm_args[@]}"

# oauth2-proxy is always deployed by the gateway sub-chart but crashes without
# OIDC configuration. Scale to 0 when auth is disabled.
kubectl scale deployment osmo-gateway-oauth2-proxy -n "$NS_OSMO_CONTROL_PLANE" --replicas=0 2>/dev/null || true

# Wait for core deployments to become available (excluding oauth2-proxy)
info "Waiting for core services to become ready..."
for deploy in osmo-gateway-envoy osmo-service osmo-agent osmo-worker osmo-logger osmo-router osmo-ui osmo-delayed-job-monitor; do
    if kubectl get deployment "$deploy" -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
        kubectl rollout status deployment "$deploy" -n "$NS_OSMO_CONTROL_PLANE" --timeout="${TIMEOUT_DEPLOY}" || \
            fatal "Deployment $deploy failed to become ready"
    fi
done

#------------------------------------------------------------------------------
# Phase 3: Post-deploy Smoke Test
#------------------------------------------------------------------------------

section "Post-deploy Smoke Test"
if [[ -z "$service_url" ]]; then
    service_url=$(detect_service_url)
fi

expected_major="${image_version%%.*}"
expected_minor="${image_version#*.}"; expected_minor="${expected_minor%%.*}"
if ! [[ "$expected_minor" =~ ^[0-9]+$ ]]; then
    fatal "Cannot parse minor version from image_version='$image_version'"
fi

version_check_passed=false
if [[ -n "$service_url" ]]; then
    if curl -sf --connect-timeout 5 "${service_url}/api/version" | jq -e --arg maj "$expected_major" --arg min "$expected_minor" '(.major | tostring) == $maj and (.minor | tonumber) >= ($min | tonumber)' >/dev/null 2>&1; then
        version_check_passed=true
        info "OSMO service healthy at ${service_url}"
    else
        warn "Cannot reach gateway at ${service_url} — verifying via in-cluster check"
    fi
fi

if [[ "$version_check_passed" != "true" ]]; then
    service_pod=$(kubectl get pods -n "$NS_OSMO_CONTROL_PLANE" -l app=osmo-service --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    [[ -n "$service_pod" ]] || fatal "No running osmo-service pod found"
    kubectl exec "$service_pod" -n "$NS_OSMO_CONTROL_PLANE" -- python3 -c "
import urllib.request, json, sys
resp = urllib.request.urlopen('http://osmo-gateway.${NS_OSMO_CONTROL_PLANE}.svc.cluster.local/api/version', timeout=10)
data = json.loads(resp.read())
if str(data.get('major')) == '${expected_major}' and int(data.get('minor', 0)) >= ${expected_minor}:
    print(f'OSMO {data[\"major\"]}.{data[\"minor\"]}.{data.get(\"revision\", \"?\")} verified via in-cluster check')
else:
    sys.exit(1)
" || fatal "Version check failed — expected ${expected_major}.${expected_minor}.x"
fi

if [[ -n "$service_url" ]]; then
    if osmo login "${service_url}/" --method dev --username admin >/dev/null 2>&1 \
        && osmo profile set pool "default" >/dev/null 2>&1; then
        info "OSMO login and profile configured"
    else
        warn "OSMO login failed — gateway may not be reachable from this host. Configure VPN for CLI access (or, in a devcontainer/codespace, port-forward the gateway: kubectl port-forward svc/osmo-gateway 9000:80 -n $NS_OSMO_CONTROL_PLANE)."
    fi
fi

# Assign required roles to admin user for backend operator connectivity.
# Dev-mode operator authenticates as "admin" — needs osmo-admin, osmo-backend, osmo-ctrl roles.
# Calls osmo-service directly (port 8000) to bypass gateway authz sidecar.
# osmo-admin is auto-assigned by the service; osmo-backend and osmo-ctrl need explicit assignment.
info "Assigning backend roles to admin user..."
service_pod=$(kubectl get pods -n "$NS_OSMO_CONTROL_PLANE" -l app=osmo-service --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -n "$service_pod" ]]; then
    timeout 30 kubectl exec "$service_pod" -n "$NS_OSMO_CONTROL_PLANE" -- python3 -c "
import urllib.request, json, ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
headers = {'Content-Type': 'application/json', 'x-osmo-user': 'admin'}
for role in ['osmo-backend', 'osmo-ctrl']:
    req = urllib.request.Request(
        'https://localhost:8000/api/auth/user/admin/roles',
        data=json.dumps({'role': role}).encode(),
        headers=headers, method='POST')
    try:
        urllib.request.urlopen(req, timeout=5, context=ctx)
    except urllib.error.HTTPError as e:
        if e.code not in (409, 422):  # 409=already assigned, 422=system role
            raise
print('Admin roles verified: osmo-admin (system), osmo-backend, osmo-ctrl')
" || warn "Role assignment failed — operator may not authenticate correctly"
fi

#------------------------------------------------------------------------------
# Phase 4: Deploy Backend Operator
#------------------------------------------------------------------------------

if [[ "$skip_backend" == "true" ]]; then
    info "Skipping backend operator deployment (--skip-backend)"
else
    section "Deploy Backend Operator"
    ensure_namespace "$NS_OSMO_OPERATOR"
    ensure_namespace "$NS_OSMO_WORKFLOWS"

    if [[ "$use_acr" == "false" ]] && is_prerelease_tag "$image_version"; then
        create_nvcr_pull_secret "$NS_OSMO_OPERATOR" "$NGC_API_KEY" "$NVCR_PULL_SECRET"
    fi

    export WORKFLOWS_NAMESPACE="$NS_OSMO_WORKFLOWS"
    export OSMO_IDENTITY_CLIENT_ID="$osmo_identity_client_id"
    envsubst < "$workflow_sa_manifest" | kubectl apply -f - >/dev/null

    backend_chart_version="$chart_version"

    # Create dummy token secret for the chart's volume mount.
    # Dev mode (--method dev) ignores the token but pydantic requires loginMethod=token|password,
    # and the chart mounts a secret volume for both modes.
    kubectl create secret generic svc-osmo-admin -n "$NS_OSMO_OPERATOR" \
        --from-literal=token="" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null

    # Create incluster-kubeconfig ConfigMap for the dev-mode operator.
    # Dev mode calls load_kube_config() (not load_incluster_config()), which reads a
    # kubeconfig 'tokenFile' ONCE at startup and never refreshes it — so an AKS projected
    # SA token (~1h TTL) goes stale and every K8s API call 401s after ~1h. Use an exec
    # credential plugin instead: the kubernetes client re-invokes it when the cached
    # credential expires, and the plugin re-reads the freshly-rotated projected token.
    # This keeps short-lived, per-pod-SA tokens (no standing credential). The plugin and
    # kubeconfig are two keys in the same ConfigMap, both mounted at /etc/kubeconfig.
    info "Creating incluster-kubeconfig ConfigMap (self-refreshing exec credential)..."
    cat > "$_WORK_DIR/incluster-config" <<'KUBECONFIG_EOF'
apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
    server: https://kubernetes.default.svc
  name: incluster
contexts:
- context:
    cluster: incluster
    user: sa
  name: incluster
current-context: incluster
users:
- name: sa
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1
      command: python3
      args:
      - /etc/kubeconfig/token-cred.py
      interactiveMode: Never
      provideClusterInfo: false
KUBECONFIG_EOF
    cat > "$_WORK_DIR/token-cred.py" <<'PYTHON_EOF'
import base64, datetime, json
_t = open('/var/run/secrets/kubernetes.io/serviceaccount/token').read().strip()
# Expire the cached credential ~5min before the projected token does, so the next
# request re-invokes this plugin and re-reads the freshly-rotated on-disk token.
try:
    _seg = _t.split('.')[1]
    _seg += '=' * (-len(_seg) % 4)
    _exp = json.loads(base64.urlsafe_b64decode(_seg))['exp'] - 300
    _ts = datetime.datetime.fromtimestamp(_exp, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
except Exception:
    _ts = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
print(json.dumps({'apiVersion': 'client.authentication.k8s.io/v1', 'kind': 'ExecCredential',
                  'status': {'token': _t, 'expirationTimestamp': _ts}}))
PYTHON_EOF
    kubectl create configmap incluster-kubeconfig -n "$NS_OSMO_OPERATOR" \
        --from-file=config="$_WORK_DIR/incluster-config" \
        --from-file=token-cred.py="$_WORK_DIR/token-cred.py" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null

    backend_helm_args=(
        --version "$backend_chart_version"
        --namespace "$NS_OSMO_OPERATOR"
        --rollback-on-failure
        --timeout "$TIMEOUT_DEPLOY"
        -f "$backend_values"
        -f "$backend_identity_values"
        --set-string "global.osmoImageTag=$image_version"
        --set-string "global.serviceUrl=http://osmo-gateway.${NS_OSMO_CONTROL_PLANE}.svc.cluster.local"
        --set-string "global.agentNamespace=$NS_OSMO_OPERATOR"
        --set-string "global.backendNamespace=$NS_OSMO_WORKFLOWS"
        --set-string "global.backendName=$backend_name"
        --set-string "serviceAccount.annotations.azure\.workload\.identity/client-id=$osmo_identity_client_id"
    )

    if [[ "$use_acr" == "true" ]]; then
        backend_helm_args+=(--set-string "global.osmoImageLocation=${acr_login_server}/osmo")
    elif is_prerelease_tag "$image_version"; then
        backend_helm_args+=(--set-string "global.imagePullSecret=$NVCR_PULL_SECRET")
    fi

    if [[ "$use_acr" == "false" && -n "${OSMO_BACKEND_CHART_SHA256:-}" ]]; then
        backend_chart_ref=$(pull_and_verify_chart "$backend_chart_ref" "$backend_chart_version" "$OSMO_BACKEND_CHART_SHA256" "$_WORK_DIR/backend-chart")
    fi

    helm upgrade --install osmo-operator "$backend_chart_ref" "${backend_helm_args[@]}"
fi

#------------------------------------------------------------------------------
# Phase 5: Deployment Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Service Chart" "$chart_version"
print_kv "Image Version" "$image_version"
print_kv "Storage Endpoint" "$endpoint"
print_kv "Container" "$container"
print_kv "PostgreSQL" "$pg_fqdn"
print_kv "Redis" "${redis_hostname:-in-cluster}"
print_kv "Service URL" "$service_url"
print_kv "Auth Mode" "workload-identity"
print_kv "Backend Name" "$backend_name"
print_kv "Backend" "$([[ $skip_backend == true ]] && echo 'skipped' || echo 'deployed')"

info "OSMO deployment complete"
