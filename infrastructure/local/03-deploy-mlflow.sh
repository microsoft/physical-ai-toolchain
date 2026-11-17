#!/usr/bin/env bash
# Deploy the MLflow tracking server and model registry for the local profile.
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
# shellcheck source=../../scripts/lib/local-cluster.sh
source "$REPO_ROOT/scripts/lib/local-cluster.sh"

MANIFESTS_DIR="$SCRIPT_DIR/manifests"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy MLflow into this K3s cluster on the local platform services: runs and the
model registry in PostgreSQL, artifacts proxied by the server into the object
store. Jobs need only the tracking URI. Run 01-deploy-platform-services.sh first.

OPTIONS:
    -h, --help               Show this help message
    --skip-smoke-test        Skip the tracking and registry round-trip check
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0")
EOF
}

skip_smoke_test=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --skip-smoke-test)  skip_smoke_test=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools kubectl jq envsubst curl sha256sum

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Kubeconfig" "$LOCAL_KUBECONFIG"
  print_kv "Context" "$LOCAL_KUBE_CONTEXT"
  print_kv "Namespace" "$LOCAL_MLFLOW_NAMESPACE"
  print_kv "Image" "$LOCAL_MLFLOW_IMAGE"
  print_kv "Tracking URI (pods)" "$PROFILE_MLFLOW_TRACKING_URI"
  print_kv "Tracking URI (host)" "$PROFILE_MLFLOW_HOST_URI"
  print_kv "Backend Store" "postgres.$LOCAL_PLATFORM_NAMESPACE (database $LOCAL_MLFLOW_DATABASE)"
  print_kv "Artifacts" "s3://$LOCAL_MLFLOW_BUCKET/artifacts, proxied by the server"
  print_kv "Smoke Test" "$([[ "$skip_smoke_test" == "true" ]] && echo skipped || echo "metrics, artifacts, and registry round-trip")"
  exit 0
fi

local_cluster_activate
local_export_profile

#------------------------------------------------------------------------------
# Preflight
#------------------------------------------------------------------------------
section "Preflight"

for secret in "$LOCAL_POSTGRES_SECRET" "$LOCAL_S3_SECRET"; do
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" get secret "$secret" >/dev/null 2>&1 \
    || fatal "Secret $LOCAL_PLATFORM_NAMESPACE/$secret not found; run 01-deploy-platform-services.sh first"
done
info "Platform services are deployed"

#------------------------------------------------------------------------------
# Namespace and Credentials
#------------------------------------------------------------------------------
section "Namespace and Credentials"

local_ensure_namespace "$LOCAL_MLFLOW_NAMESPACE"
local_derive_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_POSTGRES_SECRET" \
  "$LOCAL_MLFLOW_NAMESPACE" "$LOCAL_MLFLOW_DATABASE_SECRET" db-password=mlflow-password
local_derive_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_S3_SECRET" \
  "$LOCAL_MLFLOW_NAMESPACE" "$LOCAL_MLFLOW_STORAGE_SECRET" \
  aws-access-key-id=access-key-id aws-secret-access-key=secret-access-key
info "MLflow reads its database and object-store credentials from Secrets in $LOCAL_MLFLOW_NAMESPACE"

#------------------------------------------------------------------------------
# MLflow Server
#------------------------------------------------------------------------------
section "MLflow Server"

local_render_manifest "$MANIFESTS_DIR/mlflow.yaml" | kubectl apply -f -
kubectl -n "$LOCAL_MLFLOW_NAMESPACE" rollout status deployment/mlflow --timeout="$TIMEOUT_DEPLOY"

# The host reaches the pinned ClusterIP directly; the API rejects Host headers it does not allow.
experiments=""
for _ in $(seq 1 20); do
  experiments=$(curl --fail --silent --connect-timeout 3 \
    "$PROFILE_MLFLOW_HOST_URI/api/2.0/mlflow/experiments/search?max_results=1" 2>/dev/null) && break
  sleep 3
done
jq -e '.experiments | type == "array"' <<< "$experiments" >/dev/null \
  || fatal "MLflow did not answer the tracking API at $PROFILE_MLFLOW_HOST_URI"
info "MLflow answers at $PROFILE_MLFLOW_HOST_URI"

#------------------------------------------------------------------------------
# Smoke Test
#------------------------------------------------------------------------------

smoke_result="skipped"
if [[ "$skip_smoke_test" != "true" ]]; then
  section "Smoke Test"
  # Rerun when the server pod changes, so a new image or configuration is always exercised.
  server_pod=$(kubectl -n "$LOCAL_MLFLOW_NAMESPACE" get pods -l app.kubernetes.io/name=mlflow \
    --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.uid}')
  local_run_job "$LOCAL_MLFLOW_NAMESPACE" mlflow-smoke-test \
    "$(local_render_manifest "$MANIFESTS_DIR/mlflow-smoke-test-job.yaml")" "$server_pod"
  smoke_result=$(kubectl -n "$LOCAL_MLFLOW_NAMESPACE" logs job/mlflow-smoke-test | grep -o 'MLFLOW_RESULT=.*' | cut -d= -f2-)
  [[ -n "$smoke_result" ]] || fatal "The MLflow smoke test reported no result"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Tracking URI (pods)" "$PROFILE_MLFLOW_TRACKING_URI"
print_kv "Tracking URI (host)" "$PROFILE_MLFLOW_HOST_URI"
print_kv "Web UI" "$PROFILE_MLFLOW_HOST_URI"
print_kv "Backend Store" "postgres.$LOCAL_PLATFORM_NAMESPACE (database $LOCAL_MLFLOW_DATABASE)"
print_kv "Artifacts" "s3://$LOCAL_MLFLOW_BUCKET/artifacts"
print_kv "Smoke Test" "$smoke_result"
info "MLflow is ready"
