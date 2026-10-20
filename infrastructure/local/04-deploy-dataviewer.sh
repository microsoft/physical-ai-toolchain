#!/usr/bin/env bash
# Build the dataviewer images and run the dataviewer in the local K3s cluster without authentication.
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
VIEWER_DIR="$REPO_ROOT/data-management/viewer"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Build the dataviewer images into the local registry and run the dataviewer in this
K3s cluster on the local datasets directory, without authentication. Only this host
reaches it: through kubectl proxy, kubectl port-forward, or its pinned ClusterIP.
Run 01-deploy-platform-services.sh first.

OPTIONS:
    -h, --help                          Show this help message
    --azure-storage-account NAME        Also list datasets from this Azure Blob Storage
                                        account, through workload identity (prepared, not
                                        validated; see the README)
    --azure-client-id ID                Client ID of the identity federated with the
                                        dataviewer-backend ServiceAccount
    --azure-dataset-container NAME      Blob container with datasets (default: datasets)
    --azure-annotation-container NAME   Blob container for annotations (default: annotations)
    --config-preview                    Print configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0")
EOF
}

azure_storage_account=""
azure_client_id=""
azure_dataset_container="datasets"
azure_annotation_container="annotations"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                      show_help; exit 0 ;;
    --azure-storage-account)        azure_storage_account="$2"; shift 2 ;;
    --azure-client-id)              azure_client_id="$2"; shift 2 ;;
    --azure-dataset-container)      azure_dataset_container="$2"; shift 2 ;;
    --azure-annotation-container)   azure_annotation_container="$2"; shift 2 ;;
    --config-preview)               config_preview=true; shift ;;
    *)                              fatal "Unknown option: $1" ;;
  esac
done

require_tools kubectl jq envsubst curl docker

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local

# Local datasets are always listed; the Blob option adds an account's datasets beside them. Empty Azure
# values leave the backend on local storage.
export LOCAL_DATAVIEWER_STORAGE_BACKEND=local LOCAL_DATAVIEWER_WORKLOAD_IDENTITY=false \
  LOCAL_DATAVIEWER_AZURE_STORAGE_ACCOUNT="" LOCAL_DATAVIEWER_AZURE_CLIENT_ID="" \
  LOCAL_DATAVIEWER_AZURE_DATASET_CONTAINER="" LOCAL_DATAVIEWER_AZURE_ANNOTATION_CONTAINER=""
storage_summary="local datasets"
if [[ -n "$azure_storage_account" ]]; then
  [[ -n "$azure_client_id" ]] || fatal "--azure-storage-account needs --azure-client-id for workload identity"
  LOCAL_DATAVIEWER_STORAGE_BACKEND=azure
  LOCAL_DATAVIEWER_WORKLOAD_IDENTITY=true
  LOCAL_DATAVIEWER_AZURE_STORAGE_ACCOUNT="$azure_storage_account"
  LOCAL_DATAVIEWER_AZURE_CLIENT_ID="$azure_client_id"
  LOCAL_DATAVIEWER_AZURE_DATASET_CONTAINER="$azure_dataset_container"
  LOCAL_DATAVIEWER_AZURE_ANNOTATION_CONTAINER="$azure_annotation_container"
  storage_summary="local datasets and Azure Blob $azure_storage_account/$azure_dataset_container (annotations in $azure_annotation_container)"
fi

datasets_owner="missing"
if [[ -d "$LOCAL_DATASETS_DIR" ]]; then
  LOCAL_DATAVIEWER_UID=$(stat -c %u "$LOCAL_DATASETS_DIR")
  LOCAL_DATAVIEWER_GID=$(stat -c %g "$LOCAL_DATASETS_DIR")
  datasets_owner="uid $LOCAL_DATAVIEWER_UID, gid $LOCAL_DATAVIEWER_GID"
fi

backend_repository="$LOCAL_REGISTRY_HOST/$LOCAL_DATAVIEWER_BACKEND_REPOSITORY"
frontend_repository="$LOCAL_REGISTRY_HOST/$LOCAL_DATAVIEWER_FRONTEND_REPOSITORY"
image_tag="sha-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
[[ -z "$(git -C "$REPO_ROOT" status --porcelain -- data-management/viewer 2>/dev/null)" ]] || image_tag+="-dirty"
viewer_url="http://$LOCAL_DATAVIEWER_CLUSTER_IP/"
proxy_url="http://127.0.0.1:8001/api/v1/namespaces/$LOCAL_DATAVIEWER_NAMESPACE/services/dataviewer:http/proxy/"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Kubeconfig" "$LOCAL_KUBECONFIG"
  print_kv "Context" "$LOCAL_KUBE_CONTEXT"
  print_kv "Namespace" "$LOCAL_DATAVIEWER_NAMESPACE"
  print_kv "Datasets" "$LOCAL_DATASETS_DIR ($datasets_owner), read-write"
  print_kv "Storage" "$storage_summary"
  print_kv "Authentication" "disabled"
  print_kv "Backend Image" "$backend_repository:$image_tag (extras $LOCAL_DATAVIEWER_BACKEND_EXTRAS)"
  print_kv "Frontend Image" "$frontend_repository:$image_tag"
  print_kv "ClusterIP URL" "$viewer_url"
  print_kv "kubectl proxy URL" "$proxy_url"
  exit 0
fi

[[ -d "$LOCAL_DATASETS_DIR" ]] || fatal "Datasets directory $LOCAL_DATASETS_DIR is missing; run 00-prepare-host.sh"
[[ "$LOCAL_DATAVIEWER_UID" != "0" ]] \
  || fatal "$LOCAL_DATASETS_DIR is owned by root; the dataviewer runs as the directory's owner, which must not be root"

local_cluster_activate

#------------------------------------------------------------------------------
# Preflight
#------------------------------------------------------------------------------
section "Preflight"

# pinning-ignore: registry health probe; no downloaded artifact
curl --fail --silent --show-error --connect-timeout 3 "http://$LOCAL_REGISTRY_HOST/v2/" >/dev/null \
  || fatal "The local registry does not answer at $LOCAL_REGISTRY_HOST; run 01-deploy-platform-services.sh first"
LOCAL_CLUSTER_DNS_IP=$(kubectl -n kube-system get service kube-dns -o jsonpath='{.spec.clusterIP}')
[[ -n "$LOCAL_CLUSTER_DNS_IP" ]] || fatal "Cluster DNS Service kube-system/kube-dns has no ClusterIP"
info "Registry answers at $LOCAL_REGISTRY_HOST; cluster DNS is $LOCAL_CLUSTER_DNS_IP"

#------------------------------------------------------------------------------
# Images
#------------------------------------------------------------------------------
section "Images"

info "Building $backend_repository:$image_tag"
LOCAL_DATAVIEWER_BACKEND_IMAGE=$(local_build_and_push_image "$backend_repository" "$image_tag" \
  "$VIEWER_DIR/backend/Dockerfile" "$VIEWER_DIR/backend" \
  --build-arg "BACKEND_EXTRAS=$LOCAL_DATAVIEWER_BACKEND_EXTRAS") \
  || fatal "The backend image did not build; nothing was deployed"
# The frontend builds from the repository root for the shared npm workspace lock. Without MSAL
# build arguments the app never mounts its sign-in gate.
info "Building $frontend_repository:$image_tag"
LOCAL_DATAVIEWER_FRONTEND_IMAGE=$(local_build_and_push_image "$frontend_repository" "$image_tag" \
  "$VIEWER_DIR/frontend/Dockerfile" "$REPO_ROOT") \
  || fatal "The frontend image did not build; nothing was deployed"
info "Backend: $LOCAL_DATAVIEWER_BACKEND_IMAGE"
info "Frontend: $LOCAL_DATAVIEWER_FRONTEND_IMAGE"

#------------------------------------------------------------------------------
# Dataviewer
#------------------------------------------------------------------------------
section "Dataviewer"

local_export_profile
local_ensure_namespace "$LOCAL_DATAVIEWER_NAMESPACE"
local_render_manifest "$MANIFESTS_DIR/dataviewer.yaml" | kubectl apply -f -
for deployment in dataviewer-backend dataviewer-frontend; do
  kubectl -n "$LOCAL_DATAVIEWER_NAMESPACE" rollout status "deployment/$deployment" --timeout="$TIMEOUT_DEPLOY"
done

# The host reaches the pinned ClusterIP directly; listing datasets exercises nginx, the backend, and the mount.
datasets=""
for _ in $(seq 1 20); do
  datasets=$(curl --fail --silent --connect-timeout 3 "${viewer_url}api/datasets" 2>/dev/null) && break
  sleep 3
done
jq -e 'type == "array"' <<< "$datasets" >/dev/null \
  || fatal "The dataviewer did not list datasets at ${viewer_url}api/datasets"
dataset_count=$(jq 'length' <<< "$datasets")

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Datasets" "$dataset_count in $LOCAL_DATASETS_DIR"
print_kv "Storage" "$storage_summary"
print_kv "ClusterIP URL" "$viewer_url"
print_kv "kubectl proxy URL" "$proxy_url (after: kubectl proxy)"
print_kv "Port-forward" "kubectl -n $LOCAL_DATAVIEWER_NAMESPACE port-forward service/dataviewer 8080:80"
info "Dataviewer is ready"
