#!/usr/bin/env bash
# Connect one owned local K3s compute plane to an existing OSMO backend and pool.
# cspell:ignore crdupgrader deletecollection dockerconfigjson fromdateiso rolebindings serviceaccounts slurpfile upgrader
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load shared setup values.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"
# shellcheck source=../../../infrastructure/setup/defaults.conf
source "$REPO_ROOT/infrastructure/setup/defaults.conf"

# Explain the target identity, transport choices, and protected outputs accepted by this command.
show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME [OPTIONS]

Authenticate, retrieve exact protected inputs, and connect the owned local K3s
compute plane to an existing OSMO backend and pool. This script changes no Azure
resource, AKS cluster, Key Vault networking, Key Vault RBAC, or remote OSMO desired state.

OPTIONS:
    -h, --help                    Show this help message
    -e, --environment NAME        Existing environment name (required)
    --host-name NAME              Host identity published in the HiL catalog (required)
    --tenant-id ID                Expected Microsoft Entra tenant (required)
    --subscription ID             Expected Azure subscription (required)
    --vault-name NAME             Expected Key Vault (required)
    --transport TRANSPORT         keyvault|scp (default: keyvault)
    --scp-source-dir DIR          Protected artifact directory for SCP transport
    --kubeconfig PATH             Owned local K3s kubeconfig
    --context NAME                Local K3s context (default: $EDGE_K3S_CONTEXT)
    --input-dir DIR               Protected local artifact directory
    --azure-config-dir DIR        Isolated Azure CLI state directory
    --osmo-config-dir DIR         Isolated OSMO client profile directory
    --connection-file PATH        Non-secret successful-connection receipt
    --config-preview              Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \
      --tenant-id <tenant> --subscription <subscription> --vault-name <vault>
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \
      --tenant-id <tenant> --subscription <subscription> --vault-name <vault> \
      --transport scp --scp-source-dir /protected/hil-inputs
EOF
}

# Set defaults for the environment, local K3s target, transfer method, and isolated client state.
environment=""
host_name=""
tenant_id=""
subscription_id=""
vault_name=""
transport="keyvault"
scp_source_dir=""
kubeconfig="${HIL_KUBECONFIG:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml}"
context="$EDGE_K3S_CONTEXT"
input_dir=""
azure_config_dir=""
osmo_config_dir=""
connection_file=""
config_preview=false

# Apply explicit command-line values before resolving derived paths and validating the target.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -e|--environment)     environment="$2"; shift 2 ;;
    --host-name)          host_name="$2"; shift 2 ;;
    --tenant-id)          tenant_id="$2"; shift 2 ;;
    --subscription)       subscription_id="$2"; shift 2 ;;
    --vault-name)         vault_name="$2"; shift 2 ;;
    --transport)          transport="$2"; shift 2 ;;
    --scp-source-dir)     scp_source_dir="$2"; shift 2 ;;
    --kubeconfig)         kubeconfig="$2"; shift 2 ;;
    --context)            context="$2"; shift 2 ;;
    --input-dir)          input_dir="$2"; shift 2 ;;
    --azure-config-dir)   azure_config_dir="$2"; shift 2 ;;
    --osmo-config-dir)    osmo_config_dir="$2"; shift 2 ;;
    --connection-file)    connection_file="$2"; shift 2 ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

# Resolve local locations for downloaded artifacts and receipts.
hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"
[[ "$transport" == "keyvault" || "$transport" == "scp" ]] || fatal "--transport must be keyvault or scp"
[[ "$transport" != "scp" || -n "$scp_source_dir" ]] || fatal "--scp-source-dir is required for SCP transport"

input_dir="${input_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/environment/${environment}-${host_name}}"
azure_config_dir="${azure_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/azure/${environment}-${host_name}}"
osmo_config_dir="${osmo_config_dir:-${XDG_CONFIG_HOME:-$HOME/.config}/physical-ai-toolchain/hil/osmo/${environment}-${host_name}}"
connection_file="${connection_file:-${XDG_STATE_HOME:-$HOME/.local/state}/physical-ai-toolchain/hil/${environment}-${host_name}-connection.json}"
catalog_secret="${environment}-${host_name}-hil-catalog"

# Show the resolved connection plan and exit without authenticating, downloading, or changing Kubernetes.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "connected"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Tenant" "$tenant_id"
  print_kv "Subscription" "$subscription_id"
  print_kv "Key Vault" "$vault_name"
  print_kv "Catalog" "$catalog_secret"
  print_kv "Transfer" "$transport"
  print_kv "SCP Source" "${scp_source_dir:-not used}"
  print_kv "Input Directory" "$input_dir"
  print_kv "Azure Config" "$([[ $transport == keyvault ]] && echo "$azure_config_dir" || echo 'not used')"
  print_kv "OSMO Config" "$osmo_config_dir"
  print_kv "K3s Context" "$context"
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Connection Receipt" "$connection_file"
  print_kv "Remote Administration" "none"
  print_kv "Next" "03-run-cpu-smoke.sh"
  exit 0
fi

# Check the commands used by the deployment path.
require_tools base64 helm jq kubectl osmo
[[ "$transport" != "keyvault" ]] || require_tools az
identity_file=/var/lib/physical-ai-toolchain/k3s-identity.json

# Authenticate to the expected Azure tenant and subscription only when Key Vault is the transfer source.
if [[ "$transport" == "keyvault" ]]; then
  hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"
fi

# Retrieve the OSMO, registry, and deployment artifacts.
hil_fetch_artifacts "$transport" "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$input_dir" "$scp_source_dir" \
  osmo_token registry_config osmo_artifacts

catalog="$input_dir/catalog.json"
# Resolve catalog-declared filenames instead of accepting arbitrary paths from external input.
artifact_path() {
  local key="${1:?artifact key required}" file
  file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
  [[ "$file" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal "Catalog has an invalid file for $key"
  printf '%s/%s\n' "$input_dir" "$file"
}

# Load the files consumed by the deployment steps.
token_file=$(artifact_path osmo_token)
registry_config=$(artifact_path registry_config)
osmo_artifacts=$(artifact_path osmo_artifacts)
service_url=$(jq -r '.service_url' "$osmo_artifacts")
backend_name=$(jq -r '.backend_name' "$osmo_artifacts")
pool_name=$(jq -r '.pool_name' "$osmo_artifacts")
operator_namespace=$(jq -r '.operator_namespace' "$osmo_artifacts")
workflow_namespace=$(jq -r '.workflow_namespace' "$osmo_artifacts")
kai_ref=$(jq -r '.kai_chart.ref' "$osmo_artifacts")
kai_version=$(jq -r '.kai_chart.version' "$osmo_artifacts")
backend_ref=$(jq -r '.backend_chart.ref' "$osmo_artifacts")
backend_version=$(jq -r '.backend_chart.version' "$osmo_artifacts")
image_location=$(jq -r '.images.location' "$osmo_artifacts")
image_version=$(jq -r '.images.version' "$osmo_artifacts")
registry_host=$(jq -r '.images.registry_host' "$osmo_artifacts")

# Log in with an isolated OSMO profile and select the pool.
hil_prepare_directory "$osmo_config_dir"
export XDG_CONFIG_HOME="$osmo_config_dir"
if ! osmo config show POOL "$pool_name" >/dev/null 2>&1; then
  osmo login "$service_url" --method code
fi
osmo profile set pool "$pool_name" >/dev/null

# Keep downloaded charts and rendered manifests in a private temporary directory that is removed on exit.
work_dir=$(mktemp -d)
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT
chmod 0700 "$work_dir"

# Pull both charts used by the local scheduler and backend.
mkdir -p "$work_dir/kai"
helm pull "$kai_ref" --version "$kai_version" --destination "$work_dir/kai"
kai_chart=$(find "$work_dir/kai" -maxdepth 1 -type f -name '*.tgz' -print -quit)
if [[ "$backend_ref" == oci://* ]]; then
  chart_registry_host="${backend_ref#oci://}"
  chart_registry_host="${chart_registry_host%%/*}"
  registry_auth=$(jq -r --arg host "$chart_registry_host" '.auths[$host].auth // empty' "$registry_config")
  [[ -n "$registry_auth" ]] || fatal "Registry configuration has no credentials for $chart_registry_host"
  registry_credentials=$(printf '%s' "$registry_auth" | base64 -d)
  registry_username="${registry_credentials%%:*}"
  registry_password="${registry_credentials#*:}"
  printf '%s' "$registry_password" | helm registry login "$chart_registry_host" \
    --username "$registry_username" --password-stdin >/dev/null
  unset registry_auth registry_credentials registry_username registry_password
fi
mkdir -p "$work_dir/backend"
helm pull "$backend_ref" --version "$backend_version" --destination "$work_dir/backend"
backend_chart=$(find "$work_dir/backend" -maxdepth 1 -type f -name '*.tgz' -print -quit)

# Install or update KAI Scheduler with architecture-specific immutable images.
case "$(uname -m)" in
  x86_64)
    kai_operator_sha="$KAI_OPERATOR_SHA256_AMD64"
    kai_crd_upgrader_sha="$KAI_CRD_UPGRADER_SHA256_AMD64"
    ;;
  aarch64|arm64)
    kai_operator_sha="$KAI_OPERATOR_SHA256_ARM64"
    kai_crd_upgrader_sha="$KAI_CRD_UPGRADER_SHA256_ARM64"
    ;;
  *) fatal "Unsupported KAI image architecture: $(uname -m)" ;;
esac
kai_helm_args=(
  --namespace "$NS_KAI_SCHEDULER"
  -f "$REPO_ROOT/infrastructure/setup/values/kai-scheduler-edge.yaml"
  --set-string "operator.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${kai_operator_sha}"
  --set-string "crdupgrader.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${kai_crd_upgrader_sha}"
  --set-string "postCleanup.image.tag=${KAI_SCHEDULER_VERSION}@sha256:${kai_crd_upgrader_sha}"
)
kube_helm "$kubeconfig" "$context" upgrade --install kai-scheduler "$kai_chart" \
  --namespace "$NS_KAI_SCHEDULER" --create-namespace \
  "${kai_helm_args[@]}" \
  --wait --timeout "$TIMEOUT_DEPLOY"
# Create or update the namespaces and secrets used by local workloads.
ensure_namespace "$kubeconfig" "$context" "$operator_namespace"
ensure_namespace "$kubeconfig" "$context" "$workflow_namespace"
create_registry_pull_secret "$kubeconfig" "$context" "$operator_namespace" \
  "$registry_config" "$OSMO_HIL_PULL_SECRET" "$registry_host"
create_registry_pull_secret "$kubeconfig" "$context" "$workflow_namespace" \
  "$registry_config" "$OSMO_HIL_PULL_SECRET" "$registry_host"
kube_kubectl "$kubeconfig" "$context" create secret generic "$OSMO_HIL_TOKEN_SECRET" \
  -n "$operator_namespace" --from-file=token="$token_file" --dry-run=client -o yaml | \
  kube_kubectl "$kubeconfig" "$context" apply -f - >/dev/null

helm_args=(
  --namespace "$operator_namespace"
  -f "$REPO_ROOT/infrastructure/setup/values/osmo-edge-backend-operator.yaml"
  --set-string "global.osmoImageTag=$image_version"
  --set-string "global.osmoImageLocation=$image_location"
  --set-string "global.serviceUrl=$service_url"
  --set-string "global.agentNamespace=$operator_namespace"
  --set-string "global.backendNamespace=$workflow_namespace"
  --set-string "global.backendName=$backend_name"
  --set-string "global.accountTokenSecret=$OSMO_HIL_TOKEN_SECRET"
  --set-string "global.loginMethod=token"
  --set-string "global.imagePullSecret=$OSMO_HIL_PULL_SECRET"
)

# Install or update the backend and wait for Helm readiness.
kube_helm "$kubeconfig" "$context" upgrade --install osmo-hil-operator "$backend_chart" \
  "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"

# Replace the non-secret connection receipt after every successful run.
hil_prepare_directory "$(dirname "$connection_file")"
receipt_tmp=$(mktemp "$(dirname "$connection_file")/.connection.XXXXXX")
node_name=$(kube_kubectl "$kubeconfig" "$context" get nodes -o jsonpath='{.items[0].metadata.name}')
jq -n --arg environment "$environment" --arg host "$host_name" --arg tenant "$tenant_id" \
  --arg subscription "$subscription_id" --arg vault "$vault_name" --arg service_url "$service_url" \
  --arg backend "$backend_name" --arg pool "$pool_name" --arg kubeconfig "$kubeconfig" \
  --arg context "$context" --arg identity "$identity_file" --arg node "$node_name" --arg workflow_namespace "$workflow_namespace" \
  --arg osmo_config "$osmo_config_dir" --arg input_sha "$(calculate_sha256 "$catalog")" \
  --arg connected_at "$(date -u +%FT%TZ)" '
  {schema_version: 1, kind: "physical-ai-hil-connection", environment: $environment,
   host_name: $host, tenant_id: $tenant, subscription_id: $subscription, vault_name: $vault,
   service_url: $service_url, backend_name: $backend, pool_name: $pool,
  kubeconfig: $kubeconfig, context: $context, node_name: $node,
  k3s_identity_file: $identity, workflow_namespace: $workflow_namespace, osmo_config_dir: $osmo_config,
   catalog_sha256: $input_sha, connected_at: $connected_at}
' > "$receipt_tmp"
chmod 0600 "$receipt_tmp"
mv "$receipt_tmp" "$connection_file"

# Summarize the connected target and identify the next command.
section "Deployment Summary"
print_kv "Milestone" "connected"
print_kv "Environment" "$environment"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Service URL" "$service_url"
print_kv "K3s Context" "$context"
print_kv "Connection Receipt" "$connection_file"
print_kv "Next" "$SCRIPT_DIR/03-run-cpu-smoke.sh --connection-file $connection_file"
info "Local compute plane is connected to OSMO"
