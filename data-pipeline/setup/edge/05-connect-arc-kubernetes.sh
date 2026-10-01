#!/usr/bin/env bash
# Connect the K3s cluster to Azure Arc using an existing Azure CLI session.
# cspell:ignore jwks
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared helpers plus the Arc and K3s defaults.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

# Describe the Azure Arc target, protected kubeconfig, and optional workload identity behavior.
show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Connect a K3s cluster to Azure Arc using an existing Azure CLI session.

OPTIONS:
    -h, --help                    Show this help message
    --subscription-id ID         Azure subscription ID (required)
    --tenant-id ID               Microsoft Entra tenant ID (required)
    --resource-group NAME        Existing Arc resource group (required)
    --location LOCATION          Azure location for Arc metadata (required)
    --cluster-name NAME          Arc-enabled Kubernetes resource name (required)
    --kubeconfig PATH            Protected K3s kubeconfig (required)
    --context NAME               Explicit K3s context
    --enable-workload-identity   Enable Arc OIDC and workload identity on K3s
    --cluster-admin-object-id ID Grant cluster-admin to an Entra user or service
                                 principal object ID (repeatable)
    --cluster-admin-group-id ID  Grant cluster-admin to an Entra group object ID
                                 (repeatable)
    --cluster-admin-signed-in-user
                                 Grant cluster-admin to the signed-in Azure CLI user
    --config-preview             Print configuration and exit

Arc cluster connect presents Entra identities to K3s by object ID, so grants use
object IDs rather than user principal names. Each subject gets one idempotent
ClusterRoleBinding named arc-cluster-admin-user-<id> or arc-cluster-admin-group-<id>.

EXAMPLES:
    $(basename "$0") --subscription-id <id> --tenant-id <id> \
      --resource-group rg-edge --location westus2 \
      --cluster-name hil-lab-01-k3s --kubeconfig /protected/k3s.yaml \
      --context physical-ai-edge --enable-workload-identity \
      --cluster-admin-signed-in-user
EOF
}

# Initialize Azure identifiers, the K3s target, and the optional OIDC/workload identity switch.
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
tenant_id="${AZURE_TENANT_ID:-}"
resource_group="${ARC_RESOURCE_GROUP:-}"
location="${ARC_LOCATION:-}"
cluster_name="${ARC_CLUSTER_NAME:-}"
kubeconfig="${EDGE_KUBECONFIG:-}"
context="$EDGE_K3S_CONTEXT"
enable_workload_identity=false
config_preview=false
oidc_issuer=""
cluster_admin_object_ids=()
cluster_admin_group_ids=()
cluster_admin_signed_in_user=false
cluster_admin_subjects=()
readonly GUID_PATTERN='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

# Record one "kind:object-id" cluster-admin subject after validating the ID as a GUID.
add_cluster_admin_subject() {
  local kind="$1" id="$2" source_option="$3" subject existing
  [[ "$id" =~ $GUID_PATTERN ]] || fatal "$source_option requires a Microsoft Entra object ID (GUID): $id"
  subject="${kind}:$(printf '%s' "$id" | tr '[:upper:]' '[:lower:]')"
  for existing in ${cluster_admin_subjects[@]+"${cluster_admin_subjects[@]}"}; do
    [[ "$existing" == "$subject" ]] && return 0
  done
  cluster_admin_subjects+=("$subject")
}

# Describe the requested cluster-admin subjects for the preview and summary.
describe_cluster_admins() {
  local parts=() subject
  for subject in ${cluster_admin_subjects[@]+"${cluster_admin_subjects[@]}"}; do
    parts+=("$subject")
  done
  if [[ "$cluster_admin_signed_in_user" == "true" && "$1" == "preview" ]]; then
    parts+=("user:<signed-in Azure CLI user>")
  fi
  if (( ${#parts[@]} == 0 )); then
    echo "none"
  else
    local joined
    joined=$(printf '%s, ' "${parts[@]}")
    echo "${joined%, }"
  fi
}

# Apply command-line values before validating the Azure, K3s, and workload identity targets.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                   show_help; exit 0 ;;
    --subscription-id)           subscription_id="$2"; shift 2 ;;
    --tenant-id)                 tenant_id="$2"; shift 2 ;;
    --resource-group)            resource_group="$2"; shift 2 ;;
    --location)                  location="$2"; shift 2 ;;
    --cluster-name)              cluster_name="$2"; shift 2 ;;
    --kubeconfig)                kubeconfig="$2"; shift 2 ;;
    --context)                   context="$2"; shift 2 ;;
    --enable-workload-identity)  enable_workload_identity=true; shift ;;
    --cluster-admin-object-id)   cluster_admin_object_ids+=("$2"); shift 2 ;;
    --cluster-admin-group-id)    cluster_admin_group_ids+=("$2"); shift 2 ;;
    --cluster-admin-signed-in-user) cluster_admin_signed_in_user=true; shift ;;
    --config-preview)            config_preview=true; shift ;;
    *)                           fatal "Unknown option: $1" ;;
  esac
done

# Reject incomplete Azure and kubeconfig targets before the network preflight or Arc mutation.
[[ -n "$subscription_id" ]] || fatal "--subscription-id is required"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$resource_group" ]] || fatal "--resource-group is required"
[[ -n "$location" ]] || fatal "--location is required"
[[ -n "$cluster_name" ]] || fatal "--cluster-name is required"
[[ -n "$kubeconfig" ]] || fatal "--kubeconfig is required"
for object_id in ${cluster_admin_object_ids[@]+"${cluster_admin_object_ids[@]}"}; do
  add_cluster_admin_subject user "$object_id" "--cluster-admin-object-id"
done
for group_id in ${cluster_admin_group_ids[@]+"${cluster_admin_group_ids[@]}"}; do
  add_cluster_admin_subject group "$group_id" "--cluster-admin-group-id"
done

# Show the planned Arc connection and preflight behavior, then exit without contacting Azure or Kubernetes.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Subscription" "$subscription_id"
  print_kv "Tenant" "$tenant_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Location" "$location"
  print_kv "Arc Kubernetes" "$cluster_name"
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Context" "$context"
  print_kv "Workload Identity" "$enable_workload_identity"
  print_kv "Cluster Admins" "$(describe_cluster_admins preview)"
  print_kv "Authentication" "Azure CLI session; device-code login supported"
  exit 0
fi

# Check the commands used by the setup path.
require_tools az kubectl sudo

  # Connect or update the Arc-enabled Kubernetes resource using the selected workload identity options.
section "Connect Arc-Enabled Kubernetes"
require_az_extension connectedk8s
if [[ "$enable_workload_identity" == "true" ]]; then
  require_tools cmp
fi
export KUBECONFIG="$kubeconfig"
connect_args=(
  connectedk8s connect
  --name "$cluster_name"
  --resource-group "$resource_group"
  --location "$location"
  --subscription "$subscription_id"
  --kube-config "$kubeconfig"
  --kube-context "$context"
)
if [[ "$enable_workload_identity" == "true" ]]; then
  connect_args+=(--enable-oidc-issuer --enable-workload-identity)
fi

if az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
    --subscription "$subscription_id" >/dev/null 2>&1; then
  info "Arc-enabled Kubernetes resource already exists"
  if [[ "$enable_workload_identity" == "true" ]]; then
    az connectedk8s update --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" --kube-config "$kubeconfig" --kube-context "$context" \
      --enable-oidc-issuer --enable-workload-identity --output none
  fi
else
  az "${connect_args[@]}" --output none
fi

# Grant cluster-admin to the requested Entra identities so Arc cluster connect works on first use.
if [[ "$cluster_admin_signed_in_user" == "true" ]]; then
  signed_in_object_id=$(az ad signed-in-user show --query id -o tsv 2>/dev/null) ||
    fatal "Unable to resolve the signed-in Azure CLI user; sign in as a user or pass --cluster-admin-object-id"
  add_cluster_admin_subject user "$signed_in_object_id" "--cluster-admin-signed-in-user"
fi
if (( ${#cluster_admin_subjects[@]} > 0 )); then
  section "Grant Arc Cluster Admin"
  for subject in "${cluster_admin_subjects[@]}"; do
    subject_kind="${subject%%:*}"
    subject_id="${subject#*:}"
    binding_name="arc-cluster-admin-${subject_kind}-${subject_id}"
    kubectl --kubeconfig "$kubeconfig" --context "$context" create clusterrolebinding "$binding_name" \
      --clusterrole=cluster-admin "--${subject_kind}=${subject_id}" --dry-run=client -o yaml |
      kubectl --kubeconfig "$kubeconfig" --context "$context" apply -f -
  done
fi

# When requested, align K3s token settings with the Arc OIDC issuer.
if [[ "$enable_workload_identity" == "true" ]]; then
  for ((attempt = 1; attempt <= 60; attempt++)); do
    oidc_issuer=$(az connectedk8s show --name "$cluster_name" --resource-group "$resource_group" \
      --subscription "$subscription_id" --query oidcIssuerProfile.issuerUrl -o tsv 2>/dev/null || true)
    [[ -n "$oidc_issuer" ]] && break
    (( attempt == 60 )) && fatal "Arc OIDC issuer was not available within five minutes"
    sleep 5
  done
  k3s_config_dir=/etc/rancher/k3s/config.yaml.d
  workload_identity_config="$k3s_config_dir/90-arc-workload-identity.yaml"
  tmp_config=$(mktemp)
  trap 'rm -f "$tmp_config"' EXIT
  cat > "$tmp_config" <<EOF
kube-apiserver-arg+:
  - "service-account-issuer=$oidc_issuer"
  - "service-account-max-token-expiration=24h"
EOF
  sudo install -d -m 0755 "$k3s_config_dir"
  if ! sudo cmp -s "$tmp_config" "$workload_identity_config"; then
    sudo install -m 0600 "$tmp_config" "$workload_identity_config"
    sudo systemctl restart k3s
  fi
fi

# Report the connected Arc Kubernetes resource and whether workload identity was enabled.
section "Deployment Summary"
print_kv "Subscription" "$subscription_id"
print_kv "Resource Group" "$resource_group"
print_kv "Location" "$location"
print_kv "Arc Kubernetes" "connected"
print_kv "Workload Identity" "$enable_workload_identity"
print_kv "OIDC Issuer" "${oidc_issuer:-not configured}"
print_kv "Cluster Admins" "$(describe_cluster_admins summary)"
info "Arc-enabled Kubernetes onboarding complete"
