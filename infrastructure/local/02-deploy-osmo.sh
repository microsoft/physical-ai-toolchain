#!/usr/bin/env bash
# Deploy a standalone OSMO control plane, backend operator, and KAI Scheduler for the local profile.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../data-pipeline/setup/defaults.conf
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
# shellcheck source=../setup/defaults.conf
source "$REPO_ROOT/infrastructure/setup/defaults.conf"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"
# shellcheck source=../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"
# shellcheck source=../../scripts/lib/local-cluster.sh
source "$REPO_ROOT/scripts/lib/local-cluster.sh"
# shellcheck source=../../scripts/lib/osmo-cluster.sh
source "$REPO_ROOT/scripts/lib/osmo-cluster.sh"

MANIFESTS_DIR="$SCRIPT_DIR/manifests"
VALUES_DIR="$SCRIPT_DIR/values"
SETUP_VALUES_DIR="$REPO_ROOT/infrastructure/setup/values"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy OSMO $OSMO_IMAGE_VERSION into this K3s cluster on the local platform services, with no
Azure dependency and no attachment to another OSMO deployment:

  - KAI Scheduler, the only scheduler OSMO supports
  - the OSMO service with dev login, configured from Helm values
  - the backend operator, which registers this cluster as backend "default"

Then logs the OSMO CLI in with connect-osmo-cli.sh and runs smoke-test-osmo.sh.
Run 01-deploy-platform-services.sh first.

OPTIONS:
    -h, --help               Show this help message
    --azure-client-id ID     Let workflow pods use this managed identity for Azure; federate it
                             first with enable-azure-access.sh (default: LOCAL_OSMO_AZURE_CLIENT_ID)
    --skip-cli               Skip the OSMO CLI login, credential setup, and smoke test
    --skip-smoke-test        Skip the smoke workflow
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0")
EOF
}

skip_cli=false
skip_smoke_test=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --azure-client-id)  LOCAL_OSMO_AZURE_CLIENT_ID="$2"; shift 2 ;;
    --skip-cli)         skip_cli=true; shift ;;
    --skip-smoke-test)  skip_smoke_test=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools kubectl helm jq envsubst openssl curl sha256sum

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local
chart_version="$OSMO_CHART_VERSION"
image_version="$OSMO_IMAGE_VERSION"
service_url="$PROFILE_OSMO_URL"
in_cluster_url="http://osmo-gateway.${LOCAL_OSMO_CONTROL_PLANE_NAMESPACE}.svc.cluster.local"
backend_name="default"
export LOCAL_REGISTRY_HOSTNAME="${LOCAL_REGISTRY_HOST%%:*}"

# The workflow ServiceAccount carries the client ID, and the pool pod template opts pods in to the webhook.
export LOCAL_OSMO_WORKLOAD_IDENTITY=false
azure_identity_summary="none; workflow pods stay off Azure"
if [[ -n "$LOCAL_OSMO_AZURE_CLIENT_ID" ]]; then
  [[ "$LOCAL_OSMO_AZURE_CLIENT_ID" =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]] \
    || fatal "--azure-client-id must be a managed identity client ID (GUID)"
  LOCAL_OSMO_WORKLOAD_IDENTITY=true
  azure_identity_summary="workload identity $LOCAL_OSMO_AZURE_CLIENT_ID for system:serviceaccount:$LOCAL_OSMO_WORKFLOWS_NAMESPACE:osmo-workflow"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Kubeconfig" "$LOCAL_KUBECONFIG"
  print_kv "Context" "$LOCAL_KUBE_CONTEXT"
  print_kv "OSMO Charts" "$OSMO_SERVICE_CHART, $OSMO_BACKEND_CHART $chart_version (SHA-256 pinned)"
  print_kv "OSMO Images" "$image_version"
  print_kv "KAI Scheduler" "$KAI_SCHEDULER_VERSION"
  print_kv "Service URL" "$service_url"
  print_kv "Namespaces" "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE, $LOCAL_OSMO_OPERATOR_NAMESPACE, $LOCAL_OSMO_WORKFLOWS_NAMESPACE"
  print_kv "PostgreSQL" "postgres.$LOCAL_PLATFORM_NAMESPACE (database $LOCAL_OSMO_DATABASE)"
  print_kv "Redis" "redis.$LOCAL_PLATFORM_NAMESPACE (no TLS)"
  print_kv "Storage" "s3://$LOCAL_OSMO_BUCKET via $PROFILE_S3_ENDPOINT"
  print_kv "Pool" "$LOCAL_OSMO_POOL (platforms default, $LOCAL_OSMO_GPU_PLATFORM)"
  print_kv "Task Mounts" "$LOCAL_JOB_DATASETS_PATH (read-only), $LOCAL_JOB_CACHE_PATH"
  print_kv "Azure Identity" "$azure_identity_summary"
  print_kv "OSMO CLI" "$([[ "$skip_cli" == "true" ]] && echo skipped || echo "$LOCAL_OSMO_CONFIG_DIR")"
  print_kv "Smoke Test" "$([[ "$skip_cli" == "true" || "$skip_smoke_test" == "true" ]] && echo skipped || echo "smoke-test-osmo.sh")"
  exit 0
fi

local_cluster_activate
local_export_profile

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
chmod 0700 "$work_dir"

#------------------------------------------------------------------------------
# Preflight
#------------------------------------------------------------------------------
section "Preflight"

for secret in "$LOCAL_POSTGRES_SECRET" "$LOCAL_REDIS_SECRET" "$LOCAL_S3_SECRET"; do
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" get secret "$secret" >/dev/null 2>&1 \
    || fatal "Secret $LOCAL_PLATFORM_NAMESPACE/$secret not found; run 01-deploy-platform-services.sh first"
done
[[ -d "$LOCAL_DATASETS_DIR" && -d "$LOCAL_CACHE_DIR" ]] \
  || fatal "Data root directories are missing under $LOCAL_DATA_ROOT; run 00-prepare-host.sh first"

helm repo add osmo "$HELM_REPO_OSMO" >/dev/null 2>&1 || true
helm repo update osmo >/dev/null
service_chart=$(pull_and_verify_chart "osmo/$OSMO_SERVICE_CHART" "$chart_version" \
  "$OSMO_SERVICE_CHART_SHA256" "$work_dir/service-chart")
backend_chart=$(pull_and_verify_chart "osmo/$OSMO_BACKEND_CHART" "$chart_version" \
  "$OSMO_BACKEND_CHART_SHA256" "$work_dir/backend-chart")
kai_chart=$(pull_and_verify_chart "$HELM_REPO_KAI/kai-scheduler" "$KAI_SCHEDULER_VERSION" \
  "$KAI_SCHEDULER_CHART_SHA256" "$work_dir/kai-chart")
info "Pulled and verified the OSMO and KAI charts"

#------------------------------------------------------------------------------
# KAI Scheduler
#------------------------------------------------------------------------------
section "KAI Scheduler"

kai_image_args_text=$(kai_scheduler_image_args)
mapfile -t kai_image_args <<< "$kai_image_args_text"
install_kai_scheduler "$LOCAL_KUBECONFIG" "$LOCAL_KUBE_CONTEXT" "$kai_chart" \
  "$SETUP_VALUES_DIR/kai-scheduler-edge.yaml" "${kai_image_args[@]}"

#------------------------------------------------------------------------------
# Namespaces and Credentials
#------------------------------------------------------------------------------
section "Namespaces and Credentials"

for namespace in "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" "$LOCAL_OSMO_OPERATOR_NAMESPACE" \
  "$LOCAL_OSMO_WORKFLOWS_NAMESPACE"; do
  local_ensure_namespace "$namespace"
done

# OSMO reads its database and Redis passwords, and its storage credential, from Secrets in its own
# namespace, in field names fixed by the chart and by OSMO's credential model.
local_derive_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_POSTGRES_SECRET" \
  "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" "$SECRET_POSTGRES" db-password=osmo-password
local_derive_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_REDIS_SECRET" \
  "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" "$SECRET_REDIS" redis-password=password
local_derive_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_S3_SECRET" \
  "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" "$LOCAL_OSMO_STORAGE_SECRET" \
  access_key_id=access-key-id access_key=secret-access-key \
  -- "region=$LOCAL_S3_REGION" "override_url=$PROFILE_S3_ENDPOINT" addressing_style=path
# OSMO accepts only a 43-character default admin password, the length of its refresh tokens.
local_ensure_generated_secret "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" osmo-default-admin password=urlsafe:32

# The master encryption key protects credentials stored in PostgreSQL, so an existing key is never replaced.
mek_state=$(osmo_mek_state "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" "$SECRET_MEK") \
  || fatal "Stopping before changing the master encryption key"
if [[ "$mek_state" == "present" ]]; then
  info "Keeping the existing OSMO master encryption key"
else
  osmo_mek_configmap "$SECRET_MEK" | kubectl -n "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" apply -f - >/dev/null
  info "Generated the OSMO master encryption key"
fi

local_render_manifest "$MANIFESTS_DIR/osmo-workflow-sa.yaml" | kubectl apply -f - >/dev/null

#------------------------------------------------------------------------------
# OSMO Service
#------------------------------------------------------------------------------
section "OSMO Service"

rendered_values="$work_dir/osmo-control-plane-local.yaml"
local_render_manifest "$VALUES_DIR/osmo-control-plane-local.yaml" > "$rendered_values"
helm upgrade --install osmo "$service_chart" \
  --namespace "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" \
  --wait --timeout "$TIMEOUT_DEPLOY" \
  -f "$SETUP_VALUES_DIR/osmo-control-plane.yaml" \
  -f "$rendered_values" \
  --set-string "global.osmoImageTag=$image_version" \
  --set-string "gateway.envoy.image=$OSMO_GATEWAY_ENVOY_IMAGE"
local_render_manifest "$MANIFESTS_DIR/osmo-gateway-service.yaml" | kubectl apply -f - >/dev/null

version_json=""
for _ in $(seq 1 40); do
  version_json=$(curl --fail --silent --connect-timeout 3 "$service_url/api/version" 2>/dev/null) && break
  sleep 3
done
[[ -n "$version_json" ]] || fatal "OSMO did not answer at $service_url/api/version"
jq -e --arg version "$image_version" \
  '"\(.major).\(.minor).\(.revision)" == $version' <<< "$version_json" >/dev/null \
  || fatal "OSMO at $service_url reports $(jq -c . <<< "$version_json"), expected $image_version"
info "OSMO $image_version answers at $service_url"

#------------------------------------------------------------------------------
# Backend Operator
#------------------------------------------------------------------------------
section "Backend Operator"

# Dev login ignores the token, but the chart requires a token login method and mounts the Secret.
kubectl create secret generic svc-osmo-admin -n "$LOCAL_OSMO_OPERATOR_NAMESPACE" --from-literal=token="" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
osmo_apply_operator_kubeconfig "$LOCAL_KUBECONFIG" "$LOCAL_KUBE_CONTEXT" "$LOCAL_OSMO_OPERATOR_NAMESPACE"
helm upgrade --install osmo-operator "$backend_chart" \
  --namespace "$LOCAL_OSMO_OPERATOR_NAMESPACE" \
  --wait --timeout "$TIMEOUT_DEPLOY" \
  -f "$SETUP_VALUES_DIR/osmo-backend-operator.yaml" \
  --set-string "global.osmoImageTag=$image_version" \
  --set-string "global.serviceUrl=$in_cluster_url" \
  --set-string "global.agentNamespace=$LOCAL_OSMO_OPERATOR_NAMESPACE" \
  --set-string "global.backendNamespace=$LOCAL_OSMO_WORKFLOWS_NAMESPACE" \
  --set-string "global.backendName=$backend_name"

#------------------------------------------------------------------------------
# OSMO CLI
#------------------------------------------------------------------------------

if [[ "$skip_cli" != "true" ]]; then
  "$SCRIPT_DIR/connect-osmo-cli.sh"
  if [[ "$skip_smoke_test" != "true" ]]; then
    "$SCRIPT_DIR/smoke-test-osmo.sh"
  fi
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "OSMO" "$image_version at $service_url"
print_kv "Backend" "$backend_name (namespace $LOCAL_OSMO_WORKFLOWS_NAMESPACE)"
print_kv "Pool" "$LOCAL_OSMO_POOL (platforms default, $LOCAL_OSMO_GPU_PLATFORM)"
print_kv "Storage" "s3://$LOCAL_OSMO_BUCKET via $PROFILE_S3_ENDPOINT"
print_kv "Task Mounts" "$LOCAL_JOB_DATASETS_PATH (read-only), $LOCAL_JOB_CACHE_PATH"
print_kv "Azure Identity" "$azure_identity_summary"
print_kv "OSMO CLI" "$([[ "$skip_cli" == "true" ]] && echo skipped || echo "logged in ($LOCAL_OSMO_CONFIG_DIR)")"
print_kv "Web UI" "$service_url"
info "Local OSMO is ready"
