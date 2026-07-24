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
EOF
}

environment=""
host_name=""
tenant_id=""
subscription_id=""
vault_name=""
kubeconfig="${HIL_KUBECONFIG:-${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml}"
context="$EDGE_K3S_CONTEXT"
input_dir=""
azure_config_dir=""
osmo_config_dir=""
connection_file=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -e|--environment)     environment="$2"; shift 2 ;;
    --host-name)          host_name="$2"; shift 2 ;;
    --tenant-id)          tenant_id="$2"; shift 2 ;;
    --subscription)       subscription_id="$2"; shift 2 ;;
    --vault-name)         vault_name="$2"; shift 2 ;;
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
  print_kv "Input Directory" "$input_dir"
  print_kv "Azure Config" "$azure_config_dir"
  print_kv "OSMO Config" "$osmo_config_dir"
  print_kv "K3s Context" "$context"
  print_kv "Kubeconfig" "$kubeconfig"
  print_kv "Connection Receipt" "$connection_file"
  print_kv "Remote Administration" "none"
  print_kv "Next" "03-run-cpu-smoke.sh"
  exit 0
fi

require_tools az base64 helm jq kubectl osmo
identity_file=/var/lib/physical-ai-toolchain/k3s-identity.json

hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"

hil_fetch_artifacts "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$input_dir" \
  osmo_token osmo_token_metadata registry_config osmo_artifacts

catalog="$input_dir/catalog.json"
# Artifact filenames are fixed by the validated catalog contract, so consumers do not reparse external paths.
token_file="$input_dir/osmo-token"
token_metadata="$input_dir/osmo-token-metadata.json"
registry_config="$input_dir/registry-config.json"
osmo_artifacts="$input_dir/osmo-artifacts.json"

# Validate and extract the host-bound OSMO contract in one pass before using any value.
osmo_contract=$(jq -er --arg environment "$environment" --arg host "$host_name" '
  if (.schema_version == 1 and .kind == "physical-ai-hil-osmo" and
  .environment == $environment and .host_name == $host and
  (.service_url | type == "string" and length > 0) and
  (.backend_name | type == "string" and length > 0) and
  (.pool_name | type == "string" and length > 0) and
  (.operator_namespace | type == "string" and length > 0) and
  (.workflow_namespace | type == "string" and length > 0) and
  (.workflow_data_uri | type == "string" and
    test("^azure://[a-z0-9]{3,24}/[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?/workflows/data$")) and
  (.workload_identity | type == "object" and
    (keys | sort) == (["client_id", "federated_credential_name", "id", "tenant_id"] | sort) and
    (.id | type == "string" and length > 0) and
    (.client_id | type == "string" and length > 0) and
    (.tenant_id | type == "string" and length > 0) and
    (.federated_credential_name | type == "string" and length > 0)) and
  (.arc_cluster | type == "object" and
    (keys | sort) == (["oidc_issuer", "resource_id"] | sort) and
    (.resource_id | type == "string" and
      test("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Kubernetes/connectedClusters/[^/]+$"; "i")) and
    (.oidc_issuer | type == "string" and test("^https://[^[:space:]]+$"))) and
  (. as $contract | .workflow_service_account | type == "object" and
    (keys | sort) == (["name", "namespace", "subject"] | sort) and
    .name == "osmo-workflow" and .namespace == $contract.workflow_namespace and
    .subject == ("system:serviceaccount:" + $contract.workflow_namespace + ":osmo-workflow")) and
  (.kai_chart.ref | type == "string" and length > 0) and
  (.kai_chart.version | type == "string" and length > 0) and
  (.kai_chart.sha256 | type == "string" and test("^[0-9a-f]{64}$")) and
  (.backend_chart.ref | type == "string" and length > 0) and
  (.backend_chart.version | type == "string" and length > 0) and
  (.backend_chart.sha256 | type == "string" and test("^[0-9a-f]{64}$")) and
  (.images.location | type == "string" and length > 0) and
  (.images.version | type == "string" and length > 0) and
  (.images.registry_host | type == "string" and length > 0))
  then [
    .service_url, .backend_name, .pool_name, .operator_namespace, .workflow_namespace,
    .workflow_data_uri, .workload_identity.client_id, .workflow_service_account.name,
    .kai_chart.ref, .kai_chart.version, .kai_chart.sha256,
    .backend_chart.ref, .backend_chart.version, .backend_chart.sha256,
    .images.location, .images.version, .images.registry_host
  ] | @tsv
  else error("invalid OSMO artifact contract")
  end
' "$osmo_artifacts") || fatal "OSMO artifact contract does not match the expected host"
IFS=$'\t' read -r service_url backend_name pool_name operator_namespace workflow_namespace \
  workflow_data_uri workload_identity_client_id workflow_service_account \
  kai_ref kai_version kai_sha backend_ref backend_version backend_sha \
  image_location image_version registry_host <<< "$osmo_contract"
hil_validate_osmo_token_metadata "$token_metadata" "$token_file" \
  "$environment" "$host_name" "$backend_name"

# Bind all later Kubernetes and Helm mutations to the root-owned K3s identity created during host preparation.
hil_require_local_k3s_identity "$identity_file" "$kubeconfig" "$context"

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

# Pull the exact cataloged chart content; versions alone are not immutable.
kai_chart=$(pull_and_verify_chart "$kai_ref" "$kai_version" "$kai_sha" "$work_dir/kai")
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
backend_chart=$(pull_and_verify_chart "$backend_ref" "$backend_version" "$backend_sha" "$work_dir/backend")

# Create the workflow identity before any local Helm mutation.
ensure_namespace "$kubeconfig" "$context" "$workflow_namespace"
kube_kubectl "$kubeconfig" "$context" create serviceaccount "$workflow_service_account" \
  --namespace "$workflow_namespace" --dry-run=client -o yaml | \
  kube_kubectl "$kubeconfig" "$context" apply -f - >/dev/null
kube_kubectl "$kubeconfig" "$context" label serviceaccount "$workflow_service_account" \
  --namespace "$workflow_namespace" azure.workload.identity/use=true --overwrite >/dev/null
kube_kubectl "$kubeconfig" "$context" annotate serviceaccount "$workflow_service_account" \
  --namespace "$workflow_namespace" \
  "azure.workload.identity/client-id=$workload_identity_client_id" --overwrite >/dev/null
workflow_service_account_json=$(kube_kubectl "$kubeconfig" "$context" get serviceaccount "$workflow_service_account" \
  --namespace "$workflow_namespace" --output json)
jq -e --arg client_id "$workload_identity_client_id" '
  .metadata.labels["azure.workload.identity/use"] == "true" and
  .metadata.annotations["azure.workload.identity/client-id"] == $client_id
' <<< "$workflow_service_account_json" >/dev/null || \
  fatal "Local OSMO workflow service account does not have the required workload identity metadata"

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
  --arg osmo_config "$osmo_config_dir" --arg azure_config "$azure_config_dir" \
  --arg workflow_data_uri "$workflow_data_uri" --arg workload_identity_client_id "$workload_identity_client_id" \
  --arg input_sha "$(calculate_sha256 "$catalog")" \
  --arg connected_at "$(date -u +%FT%TZ)" '
  {schema_version: 1, kind: "physical-ai-hil-connection", environment: $environment,
   host_name: $host, tenant_id: $tenant, subscription_id: $subscription, vault_name: $vault,
   service_url: $service_url, backend_name: $backend, pool_name: $pool,
  kubeconfig: $kubeconfig, context: $context, node_name: $node,
  k3s_identity_file: $identity, workflow_namespace: $workflow_namespace, osmo_config_dir: $osmo_config,
   azure_config_dir: $azure_config, osmo_workflow_data_uri: $workflow_data_uri,
   osmo_workload_identity_client_id: $workload_identity_client_id,
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
print_kv "Workflow Data URI" "$workflow_data_uri"
print_kv "OSMO Identity Client ID" "$workload_identity_client_id"
print_kv "Service URL" "$service_url"
print_kv "K3s Context" "$context"
print_kv "Connection Receipt" "$connection_file"
print_kv "Next" "$SCRIPT_DIR/03-run-cpu-smoke.sh --connection-file $connection_file"
info "Local compute plane is connected to OSMO"
