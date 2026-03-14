#!/usr/bin/env bash
# Shared functions for 002-setup deployment scripts
# Follows k3s/Docker/Homebrew conventions for user-facing scripts

# Source repo-root .env.local for local environment overrides (not committed to git)
_common_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_env_local="$(cd "$_common_sh_dir/../../.." 2>/dev/null && pwd)/.env.local"
if [[ -f "$_env_local" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$_env_local"
  set +a
fi
unset _common_sh_dir _env_local

# Logging functions with color support (NO_COLOR standard: https://no-color.org)
if [[ -z "${NO_COLOR+x}" ]]; then
  info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
  warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
  error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }
else
  info()  { printf '[INFO]  %s\n' "$*"; }
  warn()  { printf '[WARN]  %s\n' "$*" >&2; }
  error() { printf '[ERROR] %s\n' "$*" >&2; }
fi
fatal() { error "$@"; exit 1; }

# Check for required tools
require_tools() {
  local missing=()
  for tool in "$@"; do
    command -v "$tool" &>/dev/null || missing+=("$tool")
  done
  [[ ${#missing[@]} -eq 0 ]] || fatal "Missing required tools: ${missing[*]}"
}

# Override the osmo command with the local osmo-dev.sh Bazel wrapper.
# Requires OSMO_SOURCE_DIR in .env.local or environment (see optional/osmo-dev.sh --help).
activate_local_osmo() {
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  OSMO_DEV_CLI="${lib_dir}/../optional/osmo-dev.sh"

  if [[ ! -x "$OSMO_DEV_CLI" ]]; then
    fatal "osmo-dev.sh not found at $OSMO_DEV_CLI"
  fi

  info "Using local OSMO CLI: $OSMO_DEV_CLI"

  osmo() { "$OSMO_DEV_CLI" "$@"; }
  export OSMO_DEV_CLI
  export -f osmo
}

# Ensure Azure CLI extension is installed
require_az_extension() {
  local ext="${1:?extension name required}"
  if ! az extension show --name "$ext" &>/dev/null; then
    info "Installing Azure CLI extension '$ext'..."
    az extension add --name "$ext" --yes || fatal "Failed to install Azure CLI extension '$ext'"
  fi
}

# Read terraform outputs from state file
read_terraform_outputs() {
  local tf_dir="${1:?terraform directory required}"
  [[ -d "$tf_dir" ]] || fatal "Terraform directory not found: $tf_dir"
  [[ -f "$tf_dir/terraform.tfstate" ]] || fatal "terraform.tfstate not found in $tf_dir"
  (cd "$tf_dir" && terraform output -json) || fatal "Unable to read terraform outputs"
}

# Extract value from terraform JSON output
tf_get() {
  local json="${1:?json required}" key="${2:?key required}" default="${3:-}"
  local val
  val=$(echo "$json" | jq -r ".$key // empty")
  if [[ -n "$val" ]]; then
    echo "$val"
  elif [[ -n "$default" ]]; then
    echo "$default"
  fi
}

# Require a terraform output value (fatal if missing)
tf_require() {
  local json="${1:?json required}" key="${2:?key required}" description="${3:-$key}"
  local val
  val=$(tf_get "$json" "$key")
  [[ -n "$val" ]] || fatal "$description not found in terraform outputs"
  echo "$val"
}

# Connect to AKS cluster
connect_aks() {
  local rg="${1:?resource group required}" name="${2:?cluster name required}"
  info "Connecting to AKS cluster $name..."
  az aks get-credentials --resource-group "$rg" --name "$name" --overwrite-existing
  verify_cluster_connectivity
}

# Verify kubectl can reach the cluster API server
verify_cluster_connectivity() {
  info "Verifying cluster connectivity..."
  if ! kubectl cluster-info &>/dev/null; then
    error "Cannot connect to Kubernetes cluster"
    error "For private clusters, connect via VPN first. See: deploy/001-iac/vpn/README.md"
    fatal "Cluster connectivity check failed"
  fi
  info "Cluster connectivity verified"
}

# Ensure Kubernetes namespace exists
ensure_namespace() {
  local ns="${1:?namespace required}"
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}

# Login to Azure Container Registry
login_acr() {
  local acr="${1:?acr name required}"
  info "Logging into ACR $acr..."
  az acr login --name "$acr"
}

# Auto-detect ACR name from terraform outputs
detect_acr_name() {
  local tf_output="${1:?terraform output required}"
  local acr_name
  acr_name=$(tf_get "$tf_output" "container_registry.value.name")
  [[ -n "$acr_name" ]] || fatal "--use-acr specified but container_registry output not found in terraform state"
  echo "$acr_name"
}

# Auto-detect OSMO identity client ID from terraform outputs
detect_osmo_identity() {
  local tf_output="${1:?terraform output required}"
  local client_id
  client_id=$(tf_get "$tf_output" "osmo_workload_identity.value.client_id")
  [[ -n "$client_id" ]] || fatal "osmo_workload_identity output not found in terraform state"
  echo "$client_id"
}

# Detect OSMO service URL from cluster
detect_service_url() {
  local url=""
  # Try internal load balancer first
  local lb_ip
  lb_ip=$(kubectl get svc azureml-ingress-nginx-internal-lb -n azureml \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  if [[ -n "$lb_ip" ]]; then
    url="http://${lb_ip}"
  else
    # Fallback to ClusterIP
    local cluster_ip
    cluster_ip=$(kubectl get svc azureml-ingress-nginx-controller -n azureml \
      -o jsonpath='{.spec.clusterIP}' 2>/dev/null || true)
    if [[ -n "$cluster_ip" && "$cluster_ip" != "None" ]]; then
      url="http://${cluster_ip}"
    fi
  fi
  echo "$url"
}

# Print section header
section() {
  echo
  echo "============================"
  echo "$*"
  echo "============================"
}

# Print key-value pair for summaries
print_kv() {
  printf '%-18s %s\n' "$1:" "$2"
}

# Create NVCR image pull secret for NGC-authenticated registries
create_nvcr_pull_secret() {
  local ns="${1:?namespace required}" api_key="${2:?NGC API key required}" name="${3:-nvcr-pull-secret}"
  info "Creating NVCR pull secret $name in namespace $ns..."
  # shellcheck disable=SC2016
  kubectl create secret docker-registry "$name" \
    --namespace="$ns" \
    --docker-server=nvcr.io \
    --docker-username='$oauthtoken' \
    --docker-password="$api_key" \
    --dry-run=client -o yaml | kubectl apply -f -
}

# // ===================================================================
# OSMO Preflight Validation
# // ===================================================================

is_prerelease_tag() {
  local tag="${1:?image tag required}"
  [[ "$tag" =~ (rc|beta|alpha|pre|dev) ]] || [[ "$tag" =~ ^v20[0-9]{2}\. ]]
}

validate_version_pair() {
  local chart_version="${1:?chart version required}"
  local image_version="${2:?image version required}"
  local chart_version_set="${3:-false}"
  local image_version_set="${4:-false}"
  # shellcheck disable=SC2154
  local prerelease_chart="${5:-$OSMO_PRERELEASE_CHART_VERSION}"
  # shellcheck disable=SC2154
  local prerelease_image="${6:-$OSMO_PRERELEASE_IMAGE_VERSION}"

  [[ -n "$chart_version" ]] || fatal "Chart version cannot be empty"
  [[ -n "$image_version" ]] || fatal "Image version cannot be empty"

  if [[ "$chart_version_set" != "$image_version_set" ]]; then
    fatal "Use --chart-version and --image-version together to keep a tested chart/image pair"
  fi

  if is_prerelease_tag "$image_version"; then
    if [[ "$chart_version" != "$prerelease_chart" || "$image_version" != "$prerelease_image" ]]; then
      fatal "Unsupported prerelease pair: chart=${chart_version}, image=${image_version}. Set OSMO_PRERELEASE_CHART_VERSION/OSMO_PRERELEASE_IMAGE_VERSION to a tested pair, then retry."
    fi

    # shellcheck disable=SC2154
    if [[ "$OSMO_USE_PRERELEASE" != "true" && "$chart_version_set" != "true" ]]; then
      fatal "Prerelease image requires explicit opt-in. Set OSMO_USE_PRERELEASE=true or provide both --chart-version and --image-version."
    fi
  fi
}

# // ===================================================================
# OSMO Authentication
# // ===================================================================

osmo_login_and_setup() {
  local service_url="${1:?service URL required}"
  local username="${2:-guest}"
  local roles="${3:-osmo-backend}"
  info "Logging into OSMO at ${service_url}..."
  osmo login "${service_url}/" --method dev --username "$username"
  info "Ensuring dev user '$username' exists with $roles role..."
  if ! osmo user get "$username" &>/dev/null; then
    osmo user create "$username" --roles "$roles"
  else
    osmo user update "$username" --add-roles "$roles"
  fi
}

# Apply SecretProviderClass for Azure Key Vault secrets sync
# Usage: apply_secret_provider_class <namespace> <keyvault> <client_id> <tenant_id>
apply_secret_provider_class() {
  local namespace="${1:?namespace required}"
  local keyvault="${2:?keyvault name required}"
  local client_id="${3:?client_id required}"
  local tenant_id="${4:?tenant_id required}"

  local manifest_dir
  manifest_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/manifests"

  export NAMESPACE="$namespace"
  export KEY_VAULT_NAME="$keyvault"
  export OSMO_CLIENT_ID="$client_id"
  export TENANT_ID="$tenant_id"

  info "Applying SecretProviderClass to namespace $namespace..."
  envsubst < "$manifest_dir/aks-secret-provider-class.yaml" | kubectl apply -f -
}
