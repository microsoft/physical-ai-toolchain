#!/usr/bin/env bash
# Deploy a token-authenticated OSMO 6.3 external backend to an explicit K3s context.
# cspell:ignore fromdateiso
set -o errexit -o nounset -o pipefail

_WORK_DIR=$(mktemp -d)
token_tmp=""
metadata_tmp=""
trap 'rm -rf "$_WORK_DIR"; rm -f "${token_tmp:-}" "${metadata_tmp:-}"' EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Validate AKS/K3s identities, install KAI on K3s, and deploy an external OSMO backend.
Apply the matching HiL desired-state overlay with 03-deploy-osmo.sh first.

OPTIONS:
    -h, --help                    Show this help message
    --aks-kubeconfig PATH         Existing isolated AKS kubeconfig (required)
    --aks-context NAME            Explicit AKS context (required)
    --aks-resource-id ID          Expected Azure AKS resource ID (required)
    --edge-kubeconfig PATH        Existing K3s kubeconfig (required)
    --edge-context NAME           Explicit K3s context (required)
    --edge-node-name NAME         Expected single K3s node name (required)
    --edge-k3s-version VERSION    Expected K3s kubelet version (required)
    --service-url URL             Private OSMO URL, http://RFC1918 (required)
    --backend-name NAME           Unique backend configured in OSMO (required)
    --pool-name NAME              Pool configured in OSMO (default: backend name)
    --operator-namespace NAME     Operator namespace (default: $OSMO_HIL_OPERATOR_NAMESPACE)
    --workflow-namespace NAME     Workflow namespace (default: $OSMO_HIL_WORKFLOW_NAMESPACE)
    --token-file PATH             Protected OSMO token file (required)
    --token-metadata-file PATH    Protected non-secret token metadata JSON (required)
    --osmo-config-dir PATH        Protected isolated OSMO profile for token issuance
    --registry-config-file PATH   Protected Docker config JSON for image pulls (required)
    --osmo-image-location IMAGE   OSMO image repository prefix (default: nvcr.io/nvidia/osmo)
    --image-manifest PATH         Immutable ACR image manifest for private Azure registry images
    --issue-token                 Issue a new OSMO 6.3 token through the authenticated CLI
    --service-user NAME           OSMO service user (default: svc-<backend>)
    --token-expiry YYYY-MM-DD     Token expiry (required with --issue-token)
    --chart-version VERSION       Backend chart version (default: $OSMO_CHART_VERSION)
    --backend-chart-ref REF       Backend chart reference (default: osmo/backend-operator)
    --backend-chart-sha256 SHA    Expected backend chart SHA-256
    --image-version VERSION       OSMO image version (default: $OSMO_IMAGE_VERSION)
    --config-preview              Print configuration and exit

EXAMPLES:
    $(basename "$0") --aks-kubeconfig /protected/aks.yaml --aks-context aks-dev \
      --edge-kubeconfig /protected/edge.yaml --edge-context physical-ai-edge \
      --service-url http://10.0.5.7 --backend-name hil-lab-01 \
      --token-file /protected/osmo.token --token-metadata-file /protected/osmo-token.json \
      --issue-token --token-expiry 2026-07-14
EOF
}

aks_kubeconfig=""
aks_context=""
aks_resource_id=""
edge_kubeconfig=""
edge_context=""
edge_node_name=""
edge_k3s_version=""
service_url=""
backend_name=""
pool_name=""
operator_namespace="$OSMO_HIL_OPERATOR_NAMESPACE"
workflow_namespace="$OSMO_HIL_WORKFLOW_NAMESPACE"
token_file=""
token_metadata_file=""
osmo_config_dir=""
registry_config_file=""
osmo_image_location="nvcr.io/nvidia/osmo"
image_manifest=""
issue_token=false
service_user=""
token_expiry=""
chart_version="$OSMO_CHART_VERSION"
backend_chart_ref="osmo/$OSMO_BACKEND_CHART"
backend_chart_sha256="$OSMO_BACKEND_CHART_SHA256"
image_version="$OSMO_IMAGE_VERSION"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --aks-kubeconfig)       aks_kubeconfig="$2"; shift 2 ;;
    --aks-context)          aks_context="$2"; shift 2 ;;
    --aks-resource-id)      aks_resource_id="$2"; shift 2 ;;
    --edge-kubeconfig)      edge_kubeconfig="$2"; shift 2 ;;
    --edge-context)         edge_context="$2"; shift 2 ;;
    --edge-node-name)       edge_node_name="$2"; shift 2 ;;
    --edge-k3s-version)     edge_k3s_version="$2"; shift 2 ;;
    --service-url)          service_url="$2"; shift 2 ;;
    --backend-name)         backend_name="$2"; shift 2 ;;
    --pool-name)            pool_name="$2"; shift 2 ;;
    --operator-namespace)   operator_namespace="$2"; shift 2 ;;
    --workflow-namespace)   workflow_namespace="$2"; shift 2 ;;
    --token-file)           token_file="$2"; shift 2 ;;
    --token-metadata-file)  token_metadata_file="$2"; shift 2 ;;
    --osmo-config-dir)      osmo_config_dir="$2"; shift 2 ;;
    --registry-config-file) registry_config_file="$2"; shift 2 ;;
    --osmo-image-location)  osmo_image_location="$2"; shift 2 ;;
    --image-manifest)       image_manifest="$2"; shift 2 ;;
    --issue-token)          issue_token=true; shift ;;
    --service-user)         service_user="$2"; shift 2 ;;
    --token-expiry)         token_expiry="$2"; shift 2 ;;
    --chart-version)        chart_version="$2"; shift 2 ;;
    --backend-chart-ref)    backend_chart_ref="$2"; shift 2 ;;
    --backend-chart-sha256) backend_chart_sha256="$2"; shift 2 ;;
    --image-version)        image_version="$2"; shift 2 ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

pool_name="${pool_name:-$backend_name}"
service_user="${service_user:-svc-$backend_name}"
[[ -n "$aks_kubeconfig" ]] || fatal "--aks-kubeconfig is required"
[[ -n "$aks_context" ]] || fatal "--aks-context is required"
[[ -n "$aks_resource_id" ]] || fatal "--aks-resource-id is required"
[[ -n "$edge_kubeconfig" ]] || fatal "--edge-kubeconfig is required"
[[ -n "$edge_context" ]] || fatal "--edge-context is required"
[[ -n "$edge_node_name" ]] || fatal "--edge-node-name is required"
[[ -n "$edge_k3s_version" ]] || fatal "--edge-k3s-version is required"
[[ -n "$service_url" ]] || fatal "--service-url is required"
[[ -n "$backend_name" ]] || fatal "--backend-name is required"
[[ -n "$token_file" ]] || fatal "--token-file is required"
[[ -n "$token_metadata_file" ]] || fatal "--token-metadata-file is required"
[[ -n "$osmo_config_dir" ]] || fatal "--osmo-config-dir is required"
[[ -n "$registry_config_file" ]] || fatal "--registry-config-file is required"
[[ -n "$osmo_image_location" ]] || fatal "--osmo-image-location cannot be empty"
[[ "$backend_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid backend name: $backend_name"
[[ "$pool_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid pool name: $pool_name"
[[ "$operator_namespace" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid operator namespace"
[[ "$workflow_namespace" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid workflow namespace"
[[ "$service_user" =~ ^[a-zA-Z0-9]([-a-zA-Z0-9_.]*[a-zA-Z0-9])?$ ]] || fatal "Invalid service user"
[[ "$issue_token" == "false" || "$token_expiry" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || \
  fatal "--token-expiry YYYY-MM-DD is required with --issue-token"
[[ "$backend_chart_sha256" =~ ^[0-9a-fA-F]{64}$ ]] || fatal "--backend-chart-sha256 must contain 64 hexadecimal characters"

python3 - "$service_url" <<'PYTHON'
import ipaddress
import sys
from urllib.parse import urlparse

url = urlparse(sys.argv[1])
if url.scheme != "http" or not url.hostname:
    raise SystemExit("private OSMO service URL must use http://<RFC1918-address>")
try:
    address = ipaddress.ip_address(url.hostname)
except ValueError as error:
    raise SystemExit("initial private OSMO profile requires a stable RFC1918 address, not DNS") from error
if not address.is_private:
    raise SystemExit("private OSMO service URL must use an RFC1918 address")
PYTHON

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "AKS Context" "$aks_context"
  print_kv "AKS Kubeconfig" "$aks_kubeconfig"
  print_kv "AKS Resource ID" "$aks_resource_id"
  print_kv "Edge Context" "$edge_context"
  print_kv "Edge Kubeconfig" "$edge_kubeconfig"
  print_kv "Edge Node" "$edge_node_name"
  print_kv "Edge K3s Version" "$edge_k3s_version"
  print_kv "Service URL" "$service_url"
  print_kv "Router URL" "ws://${service_url#http://}"
  print_kv "Backend" "$backend_name"
  print_kv "Pool" "$pool_name"
  print_kv "Operator Namespace" "$operator_namespace"
  print_kv "Workflow Namespace" "$workflow_namespace"
  print_kv "Token File" "$token_file"
  print_kv "Token Metadata" "$token_metadata_file"
  print_kv "OSMO Config" "${osmo_config_dir:-not used}"
  print_kv "Registry Config" "$registry_config_file"
  print_kv "OSMO Images" "$osmo_image_location"
  print_kv "Image Manifest" "${image_manifest:-not used}"
  print_kv "Issue Token" "$issue_token"
  print_kv "Service User" "$service_user"
  print_kv "Token Expiry" "${token_expiry:-existing metadata}"
  print_kv "Chart Version" "$chart_version"
  print_kv "Backend Chart" "$backend_chart_ref"
  print_kv "Chart SHA-256" "$backend_chart_sha256"
  print_kv "Image Version" "$image_version"
  exit 0
fi

require_tools az curl helm jq kubectl osmo python3
validate_version_pair "$chart_version" "$image_version" true true
require_protected_directory "$osmo_config_dir"
require_protected_directory "$(dirname "$token_file")"
require_protected_directory "$(dirname "$token_metadata_file")"
require_protected_file "$registry_config_file"
image_registry_host="${osmo_image_location%%/*}"
[[ -n "$image_registry_host" ]] || fatal "Cannot determine registry host from --osmo-image-location"
jq -e --arg host "$image_registry_host" '.auths[$host].auth | type == "string" and length > 0' \
  "$registry_config_file" >/dev/null || fatal "Registry config has no credentials for $image_registry_host"
if [[ "$backend_chart_ref" == oci://* ]]; then
  chart_registry_host="${backend_chart_ref#oci://}"
  chart_registry_host="${chart_registry_host%%/*}"
  jq -e --arg host "$chart_registry_host" '.auths[$host].auth | type == "string" and length > 0' \
    "$registry_config_file" >/dev/null || fatal "Registry config has no credentials for $chart_registry_host"
fi
if [[ "$image_registry_host" == *.azurecr.io ]]; then
  [[ -n "$image_manifest" ]] || fatal "--image-manifest is required for Azure Container Registry images"
  verify_acr_image_manifest "$image_manifest" "$image_registry_host" "$image_version"
fi
export XDG_CONFIG_HOME="$osmo_config_dir"

section "Preflight Backend Chart"
if [[ "$backend_chart_ref" == oci://* ]]; then
  registry_host="${backend_chart_ref#oci://}"
  registry_host="${registry_host%%/*}"
  registry_auth=$(jq -r --arg host "$registry_host" '.auths[$host].auth // empty' "$registry_config_file")
  registry_credentials=$(printf '%s' "$registry_auth" | base64 -d)
  registry_username="${registry_credentials%%:*}"
  registry_password="${registry_credentials#*:}"
  printf '%s' "$registry_password" | helm registry login "$registry_host" \
    --username "$registry_username" --password-stdin >/dev/null
  unset registry_auth registry_credentials registry_username registry_password
else
  helm repo add osmo "$HELM_REPO_OSMO" >/dev/null 2>&1 || true
  helm repo update osmo >/dev/null
fi
backend_chart=$(pull_and_verify_chart "$backend_chart_ref" "$chart_version" \
  "$backend_chart_sha256" "$_WORK_DIR/backend-chart")

if [[ "$issue_token" == "true" ]]; then
  section "Issue OSMO Backend Token"
  umask 077
  [[ ! -e "$token_file" && ! -L "$token_file" ]] || fatal "Token file already exists: $token_file"
  [[ ! -e "$token_metadata_file" && ! -L "$token_metadata_file" ]] || \
    fatal "Token metadata file already exists: $token_metadata_file"
  osmo user create "$service_user" --roles osmo-backend >/dev/null 2>&1 || \
    info "OSMO service user $service_user already exists"
  token_name="${backend_name}-$(date -u +%Y%m%dT%H%M%SZ)"
  token_json=$(osmo token set "$token_name" --user "$service_user" --expires-at "$token_expiry" \
    --description "External backend $backend_name" --roles osmo-backend -t json)
  token=$(jq -r '.token // empty' <<< "$token_json")
  [[ -n "$token" ]] || fatal "OSMO token response did not contain a token"
  token_tmp=$(mktemp "$(dirname "$token_file")/.osmo-token.XXXXXX")
  metadata_tmp=$(mktemp "$(dirname "$token_metadata_file")/.osmo-token-metadata.XXXXXX")
  printf '%s' "$token" > "$token_tmp"
  chmod 0600 "$token_tmp"
  jq -n --arg service_user "$service_user" --arg backend "$backend_name" \
    --arg issued_at "$(date -u +%FT%TZ)" --arg expires_at "${token_expiry}T23:59:59Z" \
    '{schema_version: 1, kind: "osmo-service-token-metadata", service_user: $service_user, roles: ["osmo-backend"], backend_name: $backend, issued_at: $issued_at, expires_at: $expires_at}' \
    > "$metadata_tmp"
  chmod 0600 "$metadata_tmp"
  mv "$token_tmp" "$token_file"
  mv "$metadata_tmp" "$token_metadata_file"
  unset token token_json
fi

require_protected_file "$token_file"
require_protected_file "$token_metadata_file"
jq -e --arg backend "$backend_name" '
  .schema_version == 1 and
  .backend_name == $backend and
  .roles == ["osmo-backend"] and
  (.expires_at | fromdateiso8601) > now
' "$token_metadata_file" >/dev/null || fatal "Token metadata is invalid, expired, or not scoped to osmo-backend"
[[ -s "$token_file" ]] || fatal "Token file is empty"

verify_kube_target "$aks_kubeconfig" "$aks_context" aks
verify_kube_target "$edge_kubeconfig" "$edge_context" k3s
verify_distinct_kube_targets "$aks_kubeconfig" "$aks_context" "$edge_kubeconfig" "$edge_context"
aks_resource_group=$(awk -F/ '{for (i = 1; i <= NF; i++) if ($i == "resourceGroups") {print $(i+1); exit}}' <<< "$aks_resource_id")
aks_resource_name=$(awk -F/ '{for (i = 1; i <= NF; i++) if ($i == "managedClusters") {print $(i+1); exit}}' <<< "$aks_resource_id")
[[ -n "$aks_resource_group" && -n "$aks_resource_name" ]] || fatal "--aks-resource-id is not a valid AKS resource ID"
aks_json=$(az aks show --resource-group "$aks_resource_group" --name "$aks_resource_name" -o json)
[[ "$(jq -r '.id | ascii_downcase' <<< "$aks_json")" == \
  "$(printf '%s' "$aks_resource_id" | tr '[:upper:]' '[:lower:]')" ]] || \
  fatal "Azure returned an AKS resource that does not match --aks-resource-id"
expected_aks_name=$(jq -r '.name' <<< "$aks_json")
expected_aks_host=$(jq -r '.privateFqdn // .fqdn // empty' <<< "$aks_json")
actual_aks_host=$(kube_api_server "$aks_kubeconfig" "$aks_context" | sed -E 's#^https?://##; s#:[0-9]+$##')
[[ -n "$expected_aks_name" && -n "$expected_aks_host" && "$actual_aks_host" == "$expected_aks_host" ]] || \
  fatal "AKS context API server does not match expected resource $aks_resource_id"
edge_node_json=$(kube_kubectl "$edge_kubeconfig" "$edge_context" get nodes -o json)
jq -e --arg node "$edge_node_name" --arg version "$edge_k3s_version" '
  .items as $items |
  ($items | length) == 1 and
  $items[0].metadata.name == $node and
  $items[0].status.nodeInfo.kubeletVersion == $version
' \
  <<< "$edge_node_json" >/dev/null || fatal "Edge context does not contain the expected single K3s node $edge_node_name"

section "Validate Private OSMO Endpoint"
version_json=$(curl -fsS --connect-timeout 5 "${service_url%/}/api/version")
jq -e --arg major "${image_version%%.*}" --arg minor "${image_version#*.}" '
  (.major | tostring) == $major and (.minor | tostring) == ($minor | split(".")[0])
' <<< "$version_json" >/dev/null || fatal "OSMO endpoint version does not match image version $image_version"

section "Validate OSMO Desired State"
osmo_values=$(kube_helm "$aks_kubeconfig" "$aks_context" get values osmo -n "$NS_OSMO_CONTROL_PLANE" -o json --all)
jq -e --arg backend "$backend_name" --arg pool "$pool_name" --arg namespace "$workflow_namespace" \
  --arg base "$service_url" --arg router "ws://${service_url#http://}" '
    .services.configs.service.service_base_url == $base and
    .services.configs.backends[$backend].k8s_namespace == $namespace and
    .services.configs.backends[$backend].router_address == $router and
    .services.configs.pools[$pool].backend == $backend
  ' <<< "$osmo_values" >/dev/null || \
  fatal "AKS OSMO release does not contain the matching backend, pool, namespace, and private endpoint"

section "Install KAI Scheduler"
kai_chart=$(pull_and_verify_chart "$HELM_REPO_KAI/kai-scheduler" "$KAI_SCHEDULER_VERSION" \
  "$KAI_SCHEDULER_CHART_SHA256" "$_WORK_DIR/kai-chart")
kube_helm "$edge_kubeconfig" "$edge_context" upgrade --install kai-scheduler "$kai_chart" \
  --namespace "$NS_KAI_SCHEDULER" --create-namespace \
  -f "$SCRIPT_DIR/values/kai-scheduler-edge.yaml" --timeout "$TIMEOUT_DEPLOY"
kube_kubectl "$edge_kubeconfig" "$edge_context" rollout status deployment/kai-operator \
  -n "$NS_KAI_SCHEDULER" --timeout "$TIMEOUT_DEPLOY"
kube_kubectl "$edge_kubeconfig" "$edge_context" wait --for=condition=Available \
  schedulingshard/default --timeout "$TIMEOUT_DEPLOY"

section "Prepare Edge Namespaces and Token"
ensure_namespace "$edge_kubeconfig" "$edge_context" "$operator_namespace"
ensure_namespace "$edge_kubeconfig" "$edge_context" "$workflow_namespace"
create_registry_pull_secret "$edge_kubeconfig" "$edge_context" "$operator_namespace" \
  "$registry_config_file" "$OSMO_HIL_PULL_SECRET" "$image_registry_host"
create_registry_pull_secret "$edge_kubeconfig" "$edge_context" "$workflow_namespace" \
  "$registry_config_file" "$OSMO_HIL_PULL_SECRET" "$image_registry_host"
kube_kubectl "$edge_kubeconfig" "$edge_context" create secret generic "$OSMO_HIL_TOKEN_SECRET" \
  -n "$operator_namespace" --from-file=token="$token_file" --dry-run=client -o yaml | \
  kube_kubectl "$edge_kubeconfig" "$edge_context" apply -f - >/dev/null

section "Deploy External Backend Operator"
helm_args=(
  --namespace "$operator_namespace"
  -f "$SCRIPT_DIR/values/osmo-edge-backend-operator.yaml"
  --set-string "global.osmoImageTag=$image_version"
  --set-string "global.osmoImageLocation=$osmo_image_location"
  --set-string "global.serviceUrl=$service_url"
  --set-string "global.agentNamespace=$operator_namespace"
  --set-string "global.backendNamespace=$workflow_namespace"
  --set-string "global.backendName=$backend_name"
  --set-string "global.accountTokenSecret=$OSMO_HIL_TOKEN_SECRET"
  --set-string "global.loginMethod=token"
  --set-string "global.imagePullSecret=$OSMO_HIL_PULL_SECRET"
)
kube_helm "$edge_kubeconfig" "$edge_context" template osmo-hil-operator "$backend_chart" \
  "${helm_args[@]}" > "$_WORK_DIR/rendered-backend.yaml"
grep -Eq '^[[:space:]]*privileged:[[:space:]]*true' "$_WORK_DIR/rendered-backend.yaml" && fatal "Rendered backend requests privileged containers"
grep -Eq '^[[:space:]]*host(Network|PID|IPC):[[:space:]]*true' "$_WORK_DIR/rendered-backend.yaml" && fatal "Rendered backend requests host namespace access"
grep -Eq '^[[:space:]]*hostPath:' "$_WORK_DIR/rendered-backend.yaml" && fatal "Rendered backend requests hostPath access"
grep -Eq '^[[:space:]]*name:[[:space:]]*cluster-admin[[:space:]]*$' "$_WORK_DIR/rendered-backend.yaml" && fatal "Rendered backend binds cluster-admin"
grep -Eq '^[[:space:]]*(verbs|resources|apiGroups):[[:space:]]*\[[^]]*"\*"' "$_WORK_DIR/rendered-backend.yaml" && \
  fatal "Rendered backend contains wildcard RBAC permissions"

if [[ "$image_registry_host" == *.azurecr.io ]]; then
  verify_acr_image_manifest "$image_manifest" "$image_registry_host" "$image_version"
fi
kube_helm "$edge_kubeconfig" "$edge_context" upgrade --install osmo-hil-operator "$backend_chart" \
  "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"

section "Validate External Backend"
kube_kubectl "$edge_kubeconfig" "$edge_context" rollout status deployment/osmo-hil-operator-osmo-backend-listener \
  -n "$operator_namespace" --timeout "$TIMEOUT_DEPLOY"
kube_kubectl "$edge_kubeconfig" "$edge_context" rollout status deployment/osmo-hil-operator-osmo-backend-worker \
  -n "$operator_namespace" --timeout "$TIMEOUT_DEPLOY"
if [[ "$image_registry_host" == *.azurecr.io ]]; then
  backend_pods=$(kube_kubectl "$edge_kubeconfig" "$edge_context" get pods -n "$operator_namespace" -o json)
  jq -e --arg listener "${osmo_image_location}/backend-listener:${image_version}" \
    --arg worker "${osmo_image_location}/backend-worker:${image_version}" '
    ([.items[].spec.containers[].image] | index($listener)) != null and
    ([.items[].spec.containers[].image] | index($worker)) != null and
    all(.items[].status.containerStatuses[]?; (.imageID // "") | test("sha256:[0-9a-f]{64}"))
  ' <<< "$backend_pods" >/dev/null || fatal "Running backend Pods do not use the approved immutable ACR tags"
fi
for service_account in backend-listener backend-worker; do
  if [[ "$(kube_kubectl "$edge_kubeconfig" "$edge_context" auth can-i '*' '*' \
      --as "system:serviceaccount:${operator_namespace}:${service_account}" --all-namespaces)" == "yes" ]]; then
    fatal "ServiceAccount $service_account has unrestricted cluster permissions"
  fi
done

backend_online=false
for ((attempt = 1; attempt <= 60; attempt++)); do
  backend_json=$(osmo config show BACKEND "$backend_name" 2>/dev/null || true)
  if jq -e --arg backend "$backend_name" '
      (.online // false) == true or
      any(.backends[]?; .name == $backend and .online == true)
    ' <<< "$backend_json" >/dev/null 2>&1; then
    backend_online=true
    break
  fi
  sleep 5
done
[[ "$backend_online" == "true" ]] || fatal "OSMO backend $backend_name did not report online within five minutes"

section "Deployment Summary"
print_kv "AKS Context" "$aks_context"
print_kv "AKS Resource" "$expected_aks_name"
print_kv "Edge Context" "$edge_context"
print_kv "Edge Node" "$edge_node_name"
print_kv "Service URL" "$service_url"
print_kv "Router URL" "ws://${service_url#http://}"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Operator Namespace" "$operator_namespace"
print_kv "Workflow Namespace" "$workflow_namespace"
print_kv "KAI Scheduler" "$KAI_SCHEDULER_VERSION"
print_kv "OSMO Chart" "$chart_version"
print_kv "OSMO Image" "$image_version"
print_kv "Image Location" "$osmo_image_location"
print_kv "Backend Status" "online"
info "External OSMO backend deployment complete"
