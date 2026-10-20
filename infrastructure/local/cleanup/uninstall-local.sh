#!/usr/bin/env bash
# Remove what the local OSMO profile deployed, keeping volumes and credentials unless asked to purge them.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../data-pipeline/setup/defaults.conf
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
# shellcheck source=../../setup/defaults.conf
source "$REPO_ROOT/infrastructure/setup/defaults.conf"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"
# shellcheck source=../../../scripts/lib/local-cluster.sh
source "$REPO_ROOT/scripts/lib/local-cluster.sh"

MANIFESTS_DIR="$SCRIPT_DIR/../manifests"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Remove the local profile's components from the K3s cluster, newest layer first.
Volumes and credential Secrets stay, so the next deploy resumes with the same
databases, objects, and images. K3s, Arc agents, the GPU device plugin, and your
datasets are never touched.

OPTIONS:
    -h, --help               Show this help message
    --purge-data             Also delete the profile namespaces with their volumes and
                             credentials: databases, buckets, and registry images (destructive)
    --prune-cache            Empty the job-owned cache directory ($LOCAL_CACHE_DIR)
                             through an in-cluster Job; run it when no jobs are running
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0")
    $(basename "$0") --prune-cache
    $(basename "$0") --purge-data
EOF
}

purge_data=false
prune_cache=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --purge-data)       purge_data=true; shift ;;
    --prune-cache)      prune_cache=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools kubectl helm jq envsubst sha256sum

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

# Removal order, newest layer first. Each component names its namespaces and a case in the removal loop.
components=(dataviewer mlflow osmo kai platform)
declare -A component_namespaces=(
  [dataviewer]="$LOCAL_DATAVIEWER_NAMESPACE"
  [mlflow]="$LOCAL_MLFLOW_NAMESPACE"
  [osmo]="$LOCAL_OSMO_WORKFLOWS_NAMESPACE $LOCAL_OSMO_OPERATOR_NAMESPACE $LOCAL_OSMO_CONTROL_PLANE_NAMESPACE"
  [kai]="$NS_KAI_SCHEDULER"
  [platform]="$LOCAL_PLATFORM_NAMESPACE"
)
declare -A component_title=([dataviewer]="Dataviewer" [mlflow]="MLflow" [osmo]="OSMO" [kai]="KAI Scheduler" [platform]="Platform Services")
selector="app.kubernetes.io/part-of=$LOCAL_PART_OF"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Kubeconfig" "$LOCAL_KUBECONFIG"
  print_kv "Context" "$LOCAL_KUBE_CONTEXT"
  for component in "${components[@]}"; do
    print_kv "Remove ${component_title[$component]}" "${component_namespaces[$component]}"
  done
  print_kv "Volumes, Secrets" "$([[ "$purge_data" == "true" ]] && echo "deleted with their namespaces" || echo kept)"
  print_kv "Cache Directory" "$([[ "$prune_cache" == "true" ]] && echo "emptied: $LOCAL_CACHE_DIR" || echo kept)"
  print_kv "Datasets" "kept: $LOCAL_DATASETS_DIR"
  exit 0
fi

local_cluster_activate
local_export_profile

#------------------------------------------------------------------------------
# Cache
#------------------------------------------------------------------------------

if [[ "$prune_cache" == "true" ]]; then
  section "Prune Cache"
  local_ensure_namespace "$LOCAL_PLATFORM_NAMESPACE"
  # A unique hash input makes every prune run, even after an earlier one succeeded.
  local_run_job "$LOCAL_PLATFORM_NAMESPACE" cache-prune \
    "$(local_render_manifest "$MANIFESTS_DIR/cache-prune-job.yaml")" "$(date +%s%N)"
fi

#------------------------------------------------------------------------------
# Components
#------------------------------------------------------------------------------

# Uninstall a Helm release when present.
remove_release() {
  local release="$1" namespace="$2"
  if helm status "$release" -n "$namespace" >/dev/null 2>&1; then
    helm uninstall "$release" -n "$namespace" --wait --timeout "$TIMEOUT_DEPLOY"
  else
    info "Helm release $release not found in $namespace"
  fi
}

# Workflow pods belong to OSMO and go first. The master encryption key and generated Secrets stay with
# their namespaces, so credentials kept in PostgreSQL remain readable after a redeploy.
remove_osmo() {
  kubectl -n "$LOCAL_OSMO_WORKFLOWS_NAMESPACE" delete pods --all --ignore-not-found --wait=true >/dev/null
  remove_release osmo-operator "$LOCAL_OSMO_OPERATOR_NAMESPACE"
  remove_release osmo "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE"
  kubectl -n "$LOCAL_OSMO_CONTROL_PLANE_NAMESPACE" delete service -l "$selector" --ignore-not-found --wait=true
}

# The dataviewer keeps its data in the datasets directory, so removing it loses nothing.
remove_dataviewer() {
  kubectl -n "$LOCAL_DATAVIEWER_NAMESPACE" delete deployment,service,configmap,serviceaccount -l "$selector" \
    --ignore-not-found --wait=true
}

# MLflow keeps runs, models, and artifacts in the platform services, so removing it loses nothing.
remove_mlflow() {
  kubectl -n "$LOCAL_MLFLOW_NAMESPACE" delete deployment,service,job -l "$selector" --ignore-not-found --wait=true
}

remove_platform() {
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" delete deployment,statefulset,service,job -l "$selector" \
    --ignore-not-found --wait=true
}

for component in "${components[@]}"; do
  section "Remove ${component_title[$component]}"
  case "$component" in
    dataviewer) remove_dataviewer ;;
    mlflow) remove_mlflow ;;
    osmo) remove_osmo ;;
    kai) remove_release kai-scheduler "$NS_KAI_SCHEDULER" ;;
    platform) remove_platform ;;
  esac
  if [[ "$purge_data" == "true" ]]; then
    for namespace in ${component_namespaces[$component]}; do
      kubectl delete namespace "$namespace" --ignore-not-found --wait=true --timeout=300s
    done
    info "Deleted namespaces ${component_namespaces[$component]} with their volumes and credentials"
  fi
done

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Removed" "${components[*]}"
print_kv "Volumes, Secrets" "$([[ "$purge_data" == "true" ]] && echo deleted || echo kept)"
print_kv "Cache Directory" "$([[ "$prune_cache" == "true" ]] && echo emptied || echo kept)"
print_kv "Datasets" "kept: $LOCAL_DATASETS_DIR"
info "Local profile removal complete"
