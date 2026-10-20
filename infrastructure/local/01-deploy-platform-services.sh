#!/usr/bin/env bash
# Deploy PostgreSQL, Valkey (Redis), RustFS (S3), and a loopback image registry for the local OSMO profile.
# cspell:ignore Hltn
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

Deploy the platform services that OSMO and MLflow use in the local profile:
PostgreSQL, Valkey (Redis protocol), RustFS (S3 protocol), and an image registry
on the host loopback. Credentials are generated once into Kubernetes Secrets and
never printed. Reruns keep data and credentials and change nothing that is
already converged.

OPTIONS:
    -h, --help               Show this help message
    --skip-smoke-test        Skip the registry pull and object round-trip checks
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

require_tools kubectl jq envsubst openssl sha256sum ss

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local
case "$LOCAL_REGISTRY_HOST" in
  localhost:*|127.0.0.1:*) ;;
  *) fatal "LOCAL_REGISTRY_HOST must be a loopback host:port, such as localhost:5000" ;;
esac
LOCAL_REGISTRY_PORT="${LOCAL_REGISTRY_HOST##*:}"
[[ "$LOCAL_REGISTRY_PORT" =~ ^[0-9]+$ ]] || fatal "LOCAL_REGISTRY_HOST must end in a port number"
LOCAL_REGISTRY_BIND_ADDRESS="127.0.0.1:$LOCAL_REGISTRY_PORT"
platform_services=(postgres valkey rustfs registry)

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Kubeconfig" "$LOCAL_KUBECONFIG"
  print_kv "Context" "$LOCAL_KUBE_CONTEXT"
  print_kv "Namespace" "$LOCAL_PLATFORM_NAMESPACE"
  print_kv "PostgreSQL" "$LOCAL_POSTGRES_IMAGE"
  print_kv "Valkey" "$LOCAL_VALKEY_IMAGE"
  print_kv "RustFS" "$LOCAL_RUSTFS_IMAGE"
  print_kv "Registry" "$LOCAL_REGISTRY_IMAGE"
  print_kv "S3 Endpoint" "$PROFILE_S3_ENDPOINT"
  print_kv "Registry Address" "$LOCAL_REGISTRY_BIND_ADDRESS (host network)"
  print_kv "Databases" "$LOCAL_OSMO_DATABASE, $LOCAL_MLFLOW_DATABASE"
  print_kv "Buckets" "$LOCAL_OSMO_BUCKET, $LOCAL_MLFLOW_BUCKET"
  print_kv "Storage Class" "$LOCAL_STORAGE_CLASS"
  print_kv "Volumes" "PostgreSQL $LOCAL_POSTGRES_STORAGE, Valkey $LOCAL_REDIS_STORAGE, RustFS $LOCAL_S3_STORAGE, registry $LOCAL_REGISTRY_STORAGE"
  print_kv "Smoke Test" "$([[ "$skip_smoke_test" == "true" ]] && echo skipped || echo "registry pull and S3 round-trip")"
  exit 0
fi

local_cluster_activate
local_export_profile

#------------------------------------------------------------------------------
# Preflight
#------------------------------------------------------------------------------
section "Preflight"

kubectl get storageclass "$LOCAL_STORAGE_CLASS" >/dev/null 2>&1 \
  || fatal "StorageClass $LOCAL_STORAGE_CLASS not found; K3s ships local-path unless it was disabled"

# The registry binds a host port, so another registry on the same port (for example the gpu-offload
# podman registry) must stop first. A running registry pod from this script already holds it.
registry_running=$(kubectl -n "$LOCAL_PLATFORM_NAMESPACE" get pods -l app.kubernetes.io/name=registry \
  --field-selector=status.phase=Running -o name 2>/dev/null || true)
if [[ -z "$registry_running" ]] && [[ -n "$(ss -Hltn "sport = :$LOCAL_REGISTRY_PORT")" ]]; then
  fatal "Port $LOCAL_REGISTRY_PORT is already in use on this host; stop the process holding it (such as the gpu-offload registry) and rerun"
fi
info "Registry port $LOCAL_REGISTRY_PORT is available"

#------------------------------------------------------------------------------
# Namespace and Credentials
#------------------------------------------------------------------------------
section "Namespace and Credentials"

local_ensure_namespace "$LOCAL_PLATFORM_NAMESPACE"
local_ensure_generated_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_POSTGRES_SECRET" \
  superuser-password=24 osmo-password=24 mlflow-password=24
local_ensure_generated_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_REDIS_SECRET" password=24
local_ensure_generated_secret "$LOCAL_PLATFORM_NAMESPACE" "$LOCAL_S3_SECRET" \
  access-key-id=10 secret-access-key=20
info "Credentials are stored in Secrets in namespace $LOCAL_PLATFORM_NAMESPACE"

#------------------------------------------------------------------------------
# Services
#------------------------------------------------------------------------------
section "Services"

for service in "${platform_services[@]}"; do
  local_render_manifest "$MANIFESTS_DIR/$service.yaml" | kubectl apply -f -
done
for workload in statefulset/postgres statefulset/valkey statefulset/rustfs deployment/registry; do
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" rollout status "$workload" --timeout="$TIMEOUT_DEPLOY"
done

#------------------------------------------------------------------------------
# Databases and Buckets
#------------------------------------------------------------------------------
section "Databases and Buckets"

# Each bootstrap reruns when its volume or credentials change, so a recreated volume is initialized again.
volume_identity() {
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" get "pvc/$1" -o jsonpath='{.metadata.uid}'
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" get "secret/$2" -o jsonpath='{.metadata.resourceVersion}'
}
local_run_job "$LOCAL_PLATFORM_NAMESPACE" postgres-bootstrap \
  "$(local_render_manifest "$MANIFESTS_DIR/postgres-bootstrap-job.yaml")" \
  "$(volume_identity data-postgres-0 "$LOCAL_POSTGRES_SECRET")"
local_run_job "$LOCAL_PLATFORM_NAMESPACE" s3-bootstrap \
  "$(local_render_manifest "$MANIFESTS_DIR/s3-bootstrap-job.yaml")" \
  "$(volume_identity data-rustfs-0 "$LOCAL_S3_SECRET")"

#------------------------------------------------------------------------------
# Smoke Test
#------------------------------------------------------------------------------

registry_check="skipped"
s3_check="skipped"
if [[ "$skip_smoke_test" != "true" ]]; then
  section "Smoke Test"

  local_run_job "$LOCAL_PLATFORM_NAMESPACE" s3-smoke-test \
    "$(local_render_manifest "$MANIFESTS_DIR/s3-smoke-test-job.yaml")" \
    "$(volume_identity data-rustfs-0 "$LOCAL_S3_SECRET")"
  s3_check="single-part and multipart objects byte-identical"

  # Build a one-layer image, push it with Docker, and run it by digest so K3s containerd must pull it.
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    smoke_tag="$LOCAL_REGISTRY_HOST/physical-ai-local/registry-smoke-test:latest"
    printf 'FROM %s\n' "$LOCAL_UTILITY_IMAGE" | docker build --quiet --tag "$smoke_tag" - >/dev/null
    smoke_digest=$(docker push "$smoke_tag" | awk '/digest: sha256:/ {for (i = 1; i <= NF; i++) if ($i == "digest:") print $(i + 1)}')
    [[ "$smoke_digest" == sha256:* ]] || fatal "Could not read the pushed digest of $smoke_tag"
    export LOCAL_REGISTRY_SMOKE_IMAGE="${smoke_tag%:*}@$smoke_digest"
    local_run_job "$LOCAL_PLATFORM_NAMESPACE" registry-smoke-test \
      "$(local_render_manifest "$MANIFESTS_DIR/registry-smoke-test-job.yaml")"
    registry_check="pulled $LOCAL_REGISTRY_SMOKE_IMAGE"
  else
    warn "Docker is not available; skipping the registry pull check"
    registry_check="skipped (Docker unavailable)"
  fi
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Namespace" "$LOCAL_PLATFORM_NAMESPACE"
print_kv "PostgreSQL" "postgres.$LOCAL_PLATFORM_NAMESPACE.svc.cluster.local:5432 (databases $LOCAL_OSMO_DATABASE, $LOCAL_MLFLOW_DATABASE)"
print_kv "Redis" "redis.$LOCAL_PLATFORM_NAMESPACE.svc.cluster.local:6379 (password auth, no TLS)"
print_kv "S3" "$PROFILE_S3_ENDPOINT (buckets $LOCAL_OSMO_BUCKET, $LOCAL_MLFLOW_BUCKET; path-style)"
print_kv "Registry" "$LOCAL_REGISTRY_HOST (host loopback)"
print_kv "Credentials" "Secrets $LOCAL_POSTGRES_SECRET, $LOCAL_REDIS_SECRET, $LOCAL_S3_SECRET"
print_kv "Registry Check" "$registry_check"
print_kv "S3 Check" "$s3_check"
info "Local platform services are ready"
