#!/usr/bin/env bash
# Resolve the active platform profile (azure or local) for OSMO submission and local tooling.
#
# Source after scripts/lib/common.sh with REPO_ROOT set. PLATFORM_PROFILE selects the profile.
# azure is the default and reproduces the Terraform-output behavior submit scripts used before
# profiles existed; local reads infrastructure/local/defaults.conf. TRACKING_BACKEND overrides
# the profile's tracking backend (azureml, mlflow, or none, as training/utils/tracking.py
# defines them). Callers validate the values their workflow needs, because an empty value means
# "use the script or template default".
# cspell:ignore azureml

# Print the selected profile name: the argument, then PLATFORM_PROFILE, then azure.
platform_profile_name() {
  echo "${1:-${PLATFORM_PROFILE:-azure}}"
}

# Print 0 when an IPv4 address lies inside a CIDR, 1 otherwise.
ipv4_in_cidr() {
  local address="$1" cidr="$2" network bits a b c d n1 n2 n3 n4 mask
  network="${cidr%/*}"
  bits="${cidr#*/}"
  IFS=. read -r a b c d <<< "$address"
  IFS=. read -r n1 n2 n3 n4 <<< "$network"
  [[ "$a$b$c$d$n1$n2$n3$n4$bits" =~ ^[0-9]+$ ]] || return 1
  mask=$(( bits == 0 ? 0 : (0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF ))
  (( ((a << 24 | b << 16 | c << 8 | d) & mask) == ((n1 << 24 | n2 << 16 | n3 << 8 | n4) & mask) ))
}

# Set PROFILE_TRACKING_BACKEND from TRACKING_BACKEND or the profile default.
_resolve_profile_tracking_backend() {
  PROFILE_TRACKING_BACKEND="$(printf '%s' "${TRACKING_BACKEND:-$1}" | tr '[:upper:]' '[:lower:]')"
  case "$PROFILE_TRACKING_BACKEND" in
    azureml|mlflow|none) ;;
    *) fatal "Unknown TRACKING_BACKEND: ${TRACKING_BACKEND} (use azureml, mlflow, or none)" ;;
  esac
}

# Load the Azure profile from exported values or, when the caller read them, Terraform outputs.
_load_azure_platform_profile() {
  local storage_account="${AZURE_STORAGE_ACCOUNT_NAME:-}"
  if [[ -z "$storage_account" ]] && declare -F get_storage_account >/dev/null; then
    storage_account="$(get_storage_account)"
  fi

  PROFILE_NAME=azure
  _resolve_profile_tracking_backend azureml
  PROFILE_OSMO_URL="${OSMO_SERVICE_URL:-}"
  # The OSMO CLI keeps its current login, as submit scripts always have.
  PROFILE_OSMO_CONFIG_DIR=""
  PROFILE_OSMO_CODE_STORAGE_URL=""
  if [[ -n "$storage_account" ]]; then
    PROFILE_OSMO_CODE_STORAGE_URL="azure://${storage_account}/${OSMO_WORKFLOW_BUCKET:-osmo}/osmo-code"
  fi
  # AzureML derives the tracking URI from the workspace at run time; an MLflow backend names its server.
  PROFILE_MLFLOW_TRACKING_URI=""
  if [[ "$PROFILE_TRACKING_BACKEND" == "mlflow" ]]; then
    PROFILE_MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-}"
  fi
  PROFILE_MLFLOW_HOST_URI=""
  PROFILE_S3_ENDPOINT=""
  PROFILE_DATASETS_HOST_DIR=""
  PROFILE_DATASETS_JOB_DIR=""
  PROFILE_CACHE_HOST_DIR=""
  PROFILE_IMAGE_REGISTRY=""
  # Kubernetes clients keep the caller's kubeconfig and context.
  PROFILE_KUBECONFIG=""
  PROFILE_KUBE_CONTEXT=""
  PROFILE_OSMO_POOL="${OSMO_POOL:-default}"
  PROFILE_OSMO_GPU_PLATFORM="${OSMO_PLATFORM:-gpu_platform}"
  PROFILE_TASK_CPU=""
  PROFILE_TASK_MEMORY=""
  PROFILE_TASK_STORAGE=""
  PROFILE_TASK_GPU=""
}

# Load the local profile from infrastructure/local/defaults.conf and validate its ClusterIPs.
_load_local_platform_profile() {
  local repo_root="$1" address
  # shellcheck source=../../data-pipeline/setup/defaults.conf
  source "$repo_root/data-pipeline/setup/defaults.conf"
  # shellcheck source=../../infrastructure/local/defaults.conf
  source "$repo_root/infrastructure/local/defaults.conf"

  for address in "$LOCAL_S3_CLUSTER_IP" "$LOCAL_OSMO_GATEWAY_CLUSTER_IP" \
    "$LOCAL_MLFLOW_CLUSTER_IP" "$LOCAL_DATAVIEWER_CLUSTER_IP"; do
    ipv4_in_cidr "$address" "$EDGE_K3S_SERVICE_CIDR" \
      || fatal "Local ClusterIP $address is outside the K3s service CIDR $EDGE_K3S_SERVICE_CIDR"
  done

  PROFILE_NAME=local
  _resolve_profile_tracking_backend mlflow
  PROFILE_OSMO_URL="http://${LOCAL_OSMO_GATEWAY_CLUSTER_IP}"
  PROFILE_OSMO_CONFIG_DIR="$LOCAL_OSMO_CONFIG_DIR"
  PROFILE_OSMO_CODE_STORAGE_URL="s3://${LOCAL_OSMO_BUCKET}/osmo-code"
  PROFILE_MLFLOW_TRACKING_URI="http://mlflow.${LOCAL_MLFLOW_NAMESPACE}.svc.cluster.local:${LOCAL_MLFLOW_PORT}"
  PROFILE_MLFLOW_HOST_URI="http://${LOCAL_MLFLOW_CLUSTER_IP}:${LOCAL_MLFLOW_PORT}"
  PROFILE_S3_ENDPOINT="http://${LOCAL_S3_CLUSTER_IP}:${LOCAL_S3_PORT}"
  PROFILE_DATASETS_HOST_DIR="$LOCAL_DATASETS_DIR"
  # The OSMO pool pod template mounts the datasets directory read-only at this path in every task.
  PROFILE_DATASETS_JOB_DIR="$LOCAL_JOB_DATASETS_PATH"
  PROFILE_CACHE_HOST_DIR="$LOCAL_CACHE_DIR"
  PROFILE_IMAGE_REGISTRY="$LOCAL_REGISTRY_HOST"
  # The protected kubeconfig the K3s installer writes, so an ambient KUBECONFIG never redirects a client.
  PROFILE_KUBECONFIG="$LOCAL_KUBECONFIG"
  PROFILE_KUBE_CONTEXT="$LOCAL_KUBE_CONTEXT"
  PROFILE_OSMO_POOL="$LOCAL_OSMO_POOL"
  PROFILE_OSMO_GPU_PLATFORM="$LOCAL_OSMO_GPU_PLATFORM"
  PROFILE_TASK_CPU="$LOCAL_TASK_CPU"
  PROFILE_TASK_MEMORY="$LOCAL_TASK_MEMORY"
  PROFILE_TASK_STORAGE="$LOCAL_TASK_STORAGE"
  PROFILE_TASK_GPU="$LOCAL_TASK_GPU"
}

# Load the named (or selected) profile into PROFILE_* variables.
load_platform_profile() {
  local name
  name="$(platform_profile_name "${1:-}")"
  case "$name" in
    azure) _load_azure_platform_profile ;;
    local) _load_local_platform_profile "${REPO_ROOT:?REPO_ROOT is required to load a platform profile}" ;;
    *) fatal "Unknown platform profile: $name (use azure or local)" ;;
  esac
  if [[ "$PROFILE_TRACKING_BACKEND" == "mlflow" && -z "$PROFILE_MLFLOW_TRACKING_URI" ]]; then
    fatal "TRACKING_BACKEND=mlflow needs MLFLOW_TRACKING_URI for the $PROFILE_NAME profile"
  fi
}

#------------------------------------------------------------------------------
# OSMO submission helpers (call after load_platform_profile)
#------------------------------------------------------------------------------

# Print the profile values a submit script resolves, for --config-preview.
print_platform_profile_preview() {
  print_kv "Platform Profile" "$PROFILE_NAME"
  print_kv "OSMO Service" "${PROFILE_OSMO_URL:-<current CLI login>}"
  print_kv "Code Storage" "${PROFILE_OSMO_CODE_STORAGE_URL:-<not set>}"
  print_kv "Tracking Backend" "$PROFILE_TRACKING_BACKEND"
  print_kv "MLflow URI" "${PROFILE_MLFLOW_TRACKING_URI:-<backend default>}"
}

# Fail unless the profile can stage code; the Azure profile derives the URL from the storage account.
require_platform_code_storage() {
  [[ -n "$PROFILE_OSMO_CODE_STORAGE_URL" ]] && return 0
  fatal "Code storage is not configured for the $PROFILE_NAME profile (set AZURE_STORAGE_ACCOUNT_NAME or deploy infra)"
}

# Point the OSMO CLI at the profile's login and confirm it targets the profile's service, so a
# local submission never goes to another OSMO. The Azure profile keeps the current login.
use_platform_osmo_login() {
  [[ -n "${PROFILE_OSMO_CONFIG_DIR:-}" ]] || return 0
  export OSMO_CONFIG_FILE_DIR="$PROFILE_OSMO_CONFIG_DIR"
  local current_url
  current_url="$(osmo_login_url "$PROFILE_OSMO_CONFIG_DIR")"
  if [[ "${current_url%/}" != "${PROFILE_OSMO_URL%/}" ]]; then
    fatal "The OSMO CLI in $PROFILE_OSMO_CONFIG_DIR is logged in to ${current_url:-no service}, not $PROFILE_OSMO_URL; run infrastructure/local/connect-osmo-cli.sh"
  fi
}

# Print true when a workflow that downloads from the Hugging Face Hub should mount the OSMO huggingface
# credential: the workflow reads the Hub and the service holds one. Without it, public repositories download
# anonymously. Call after use_platform_osmo_login.
# Usage: platform_huggingface_credential <true|false>
platform_huggingface_credential() {
  if [[ "${1:-false}" != "true" ]]; then
    echo false
  elif osmo_has_credential huggingface; then
    echo true
  else
    warn "OSMO has no huggingface credential; Hub downloads run anonymously, so only public repositories work"
    echo false
  fi
}

# Resolve a dataset under the profile's datasets directory and print its path inside the job.
# Fails unless the profile mounts datasets and the directory holds a LeRobot dataset.
# Usage: job_path=$(platform_local_dataset_path <name>)
platform_local_dataset_path() {
  local name="${1:?dataset name required}"
  [[ -n "${PROFILE_DATASETS_HOST_DIR:-}" && -n "${PROFILE_DATASETS_JOB_DIR:-}" ]] \
    || fatal "Local datasets need a profile that mounts them; the $PROFILE_NAME profile does not (use --profile local)"
  [[ "$name" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ && "/$name/" != */../* && "/$name/" != */./* ]] \
    || fatal "Invalid dataset name '$name': use a relative path of letters, digits, '.', '_', and '-'"
  [[ -f "$PROFILE_DATASETS_HOST_DIR/$name/meta/info.json" ]] \
    || fatal "No LeRobot dataset at $PROFILE_DATASETS_HOST_DIR/$name (expected meta/info.json)"
  echo "$PROFILE_DATASETS_JOB_DIR/$name"
}

# Print the tracking values every profile-aware workflow template accepts, one key=value per line.
platform_tracking_workflow_values() {
  printf '%s\n' \
    "tracking_backend=$PROFILE_TRACKING_BACKEND" \
    "mlflow_tracking_uri=$PROFILE_MLFLOW_TRACKING_URI"
}

# Print the loaded profile as KEY=VALUE lines, one per value, for non-shell consumers.
platform_profile_env() {
  cat << EOF
PROFILE_NAME=${PROFILE_NAME-}
PROFILE_TRACKING_BACKEND=${PROFILE_TRACKING_BACKEND-}
PROFILE_OSMO_URL=${PROFILE_OSMO_URL-}
PROFILE_OSMO_CONFIG_DIR=${PROFILE_OSMO_CONFIG_DIR-}
PROFILE_OSMO_CODE_STORAGE_URL=${PROFILE_OSMO_CODE_STORAGE_URL-}
PROFILE_MLFLOW_TRACKING_URI=${PROFILE_MLFLOW_TRACKING_URI-}
PROFILE_MLFLOW_HOST_URI=${PROFILE_MLFLOW_HOST_URI-}
PROFILE_S3_ENDPOINT=${PROFILE_S3_ENDPOINT-}
PROFILE_DATASETS_HOST_DIR=${PROFILE_DATASETS_HOST_DIR-}
PROFILE_DATASETS_JOB_DIR=${PROFILE_DATASETS_JOB_DIR-}
PROFILE_CACHE_HOST_DIR=${PROFILE_CACHE_HOST_DIR-}
PROFILE_IMAGE_REGISTRY=${PROFILE_IMAGE_REGISTRY-}
PROFILE_KUBECONFIG=${PROFILE_KUBECONFIG-}
PROFILE_KUBE_CONTEXT=${PROFILE_KUBE_CONTEXT-}
PROFILE_OSMO_POOL=${PROFILE_OSMO_POOL-}
PROFILE_OSMO_GPU_PLATFORM=${PROFILE_OSMO_GPU_PLATFORM-}
PROFILE_TASK_CPU=${PROFILE_TASK_CPU-}
PROFILE_TASK_MEMORY=${PROFILE_TASK_MEMORY-}
PROFILE_TASK_STORAGE=${PROFILE_TASK_STORAGE-}
PROFILE_TASK_GPU=${PROFILE_TASK_GPU-}
EOF
}
