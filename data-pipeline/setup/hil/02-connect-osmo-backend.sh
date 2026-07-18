#!/usr/bin/env bash
# Connect one owned local K3s compute plane to an existing OSMO backend and pool.
# cspell:ignore crdupgrader deletecollection dockerconfigjson fromdateiso rolebindings serviceaccounts slurpfile upgrader
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load the shared safety helpers and pinned setup values.
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

# Validate the target identity and choose protected local locations for downloaded artifacts and receipts.
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

# Establish failure reporting, confirm the owned K3s identity, and reject unsafe input locations.
operation="validate local connection inputs"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: connected"
  exit "$status"
}
trap report_failure ERR

require_tools base64 curl helm jq kubectl osmo
[[ "$transport" != "keyvault" ]] || require_tools az
identity_file=/var/lib/physical-ai-toolchain/k3s-identity.json
require_external_runtime_path "$kubeconfig"
hil_require_local_k3s_identity "$identity_file" "$kubeconfig" "$context"
require_external_runtime_path "$input_dir"
require_external_runtime_path "$osmo_config_dir"
require_external_runtime_path "$connection_file"
if [[ "$transport" == "scp" ]]; then
  require_protected_directory "$scp_source_dir"
fi

# Reuse an existing receipt only when it describes this exact environment, host, and K3s target.
if [[ -e "$connection_file" ]]; then
  require_protected_file "$connection_file"
  jq -e --arg environment "$environment" --arg host "$host_name" \
    --arg tenant "$tenant_id" --arg subscription "$subscription_id" --arg vault "$vault_name" \
    --arg kubeconfig "$kubeconfig" --arg context "$context" '
    .schema_version == 1 and .kind == "physical-ai-hil-connection" and
    .environment == $environment and .host_name == $host and
    ((.tenant_id // "") | ascii_downcase) == ($tenant | ascii_downcase) and
    ((.subscription_id // "") | ascii_downcase) == ($subscription | ascii_downcase) and
    .vault_name == $vault and .kubeconfig == $kubeconfig and .context == $context
  ' "$connection_file" >/dev/null || fatal "Existing connection receipt identifies a different target"
fi

# Authenticate to the expected Azure tenant and subscription only when Key Vault is the transfer source.
if [[ "$transport" == "keyvault" ]]; then
  operation="authenticate to the expected Azure account"
  hil_login_azure "$tenant_id" "$subscription_id" "$azure_config_dir"
fi

# Retrieve the catalog and all approved OSMO, registry, image, and deployment artifacts into protected storage.
operation="retrieve exact HiL artifacts"
hil_fetch_artifacts "$transport" "$catalog_secret" "$environment" "$host_name" \
  "$tenant_id" "$subscription_id" "$vault_name" "$input_dir" "$scp_source_dir" \
  deployment image_manifest osmo_token osmo_token_metadata registry_config osmo_artifacts

catalog="$input_dir/catalog.json"
# Resolve catalog-declared filenames instead of accepting arbitrary paths from external input.
artifact_path() {
  local key="${1:?artifact key required}" file
  file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
  [[ "$file" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fatal "Catalog has an invalid file for $key"
  printf '%s/%s\n' "$input_dir" "$file"
}

# Load the protected files that the remaining validation and deployment steps will consume.
deployment_file=$(artifact_path deployment)
image_manifest=$(artifact_path image_manifest)
token_file=$(artifact_path osmo_token)
token_metadata=$(artifact_path osmo_token_metadata)
registry_config=$(artifact_path registry_config)
osmo_artifacts=$(artifact_path osmo_artifacts)
for file in "$deployment_file" "$image_manifest" "$token_file" "$token_metadata" "$registry_config" "$osmo_artifacts"; do
  require_protected_file "$file"
done

# Check that every artifact is bound to the selected environment, host, service, backend, registry, and image set.
operation="validate target-bound HiL artifacts"
jq -e --arg environment "$environment" --arg host "$host_name" '
  .schema_version == 1 and .kind == "physical-ai-hil-osmo" and
  .environment == $environment and .host_name == $host and
  (.service_url | test("^https?://[^[:space:]]+$")) and
  (.service_version.major | test("^[0-9]+$")) and (.service_version.minor | test("^[0-9]+$")) and
  (.backend_name | test("^[a-z0-9-]+$")) and
  (.pool_name | test("^[a-z0-9-]+$")) and
  (.kai_chart.sha256 | test("^[0-9a-f]{64}$")) and
  (.backend_chart.sha256 | test("^[0-9a-f]{64}$"))
' "$osmo_artifacts" >/dev/null || fatal "OSMO artifact contract does not match the selected environment and host"
service_url=$(jq -r '.service_url' "$osmo_artifacts")
if [[ "$service_url" == http://* ]]; then
  service_host="${service_url#http://}"
  service_host="${service_host%%/*}"
  service_host="${service_host%%:*}"
  [[ "$service_host" =~ ^10\. || "$service_host" =~ ^192\.168\. || \
     "$service_host" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]] || fatal "HTTP OSMO URLs must use an RFC1918 address"
elif [[ "$service_url" != https://* ]]; then
  fatal "OSMO URL must use HTTPS or private RFC1918 HTTP"
fi
service_major=$(jq -r '.service_version.major' "$osmo_artifacts")
service_minor=$(jq -r '.service_version.minor' "$osmo_artifacts")
backend_name=$(jq -r '.backend_name' "$osmo_artifacts")
pool_name=$(jq -r '.pool_name' "$osmo_artifacts")
operator_namespace=$(jq -r '.operator_namespace' "$osmo_artifacts")
workflow_namespace=$(jq -r '.workflow_namespace' "$osmo_artifacts")
kai_ref=$(jq -r '.kai_chart.ref' "$osmo_artifacts")
kai_version=$(jq -r '.kai_chart.version' "$osmo_artifacts")
kai_sha=$(jq -r '.kai_chart.sha256' "$osmo_artifacts")
backend_ref=$(jq -r '.backend_chart.ref' "$osmo_artifacts")
backend_version=$(jq -r '.backend_chart.version' "$osmo_artifacts")
backend_sha=$(jq -r '.backend_chart.sha256' "$osmo_artifacts")
image_location=$(jq -r '.images.location' "$osmo_artifacts")
image_version=$(jq -r '.images.version' "$osmo_artifacts")
registry_host=$(jq -r '.images.registry_host' "$osmo_artifacts")
jq -e --arg environment "$environment" --arg vault "$vault_name" --arg subscription "$subscription_id" \
  --arg service_url "$service_url" '
  .schema_version == 1 and .environment == $environment and .key_vault_name == $vault and
  ((.subscription_id // "") | ascii_downcase) == ($subscription | ascii_downcase) and
  .osmo_service_url == $service_url
' "$deployment_file" >/dev/null || fatal "Environment bundle does not match the selected connection"
token_sha=$(calculate_sha256 "$token_file")
jq -e --arg environment "$environment" --arg host "$host_name" --arg backend "$backend_name" \
  --arg sha "$token_sha" '
  .schema_version == 1 and .kind == "physical-ai-osmo-service-token" and
  .environment == $environment and .host_name == $host and .backend_name == $backend and
  .service == true and .roles == ["osmo-backend"] and .token_sha256 == $sha and
  (.expires_at | fromdateiso8601) > now
' "$token_metadata" >/dev/null || fatal "OSMO token metadata is expired or does not match the backend and token"
jq -e --arg host "$registry_host" '
  (.auths | keys) == [$host] and (.auths[$host].auth | type == "string" and length > 0)
' "$registry_config" >/dev/null || fatal "Registry configuration must contain only the expected pull registry"
jq -e --arg host "$registry_host" --arg version "$image_version" '
  .schema_version == 1 and .login_server == $host and .image_version == $version and
  ((.images | keys | sort) == (["agent", "backend-listener", "backend-worker", "client",
    "delayed-job-monitor", "init-container", "logger", "router", "service", "web-ui", "worker"] | sort)) and
  all(.images[]; (.digest | test("^sha256:[0-9a-f]{64}$")))
' "$image_manifest" >/dev/null || fatal "OSMO image manifest does not match the expected registry and version"

# Log in with an isolated OSMO profile, select the approved pool, and verify the remote service contract.
operation="authenticate the isolated OSMO client"
hil_prepare_directory "$osmo_config_dir"
export XDG_CONFIG_HOME="$osmo_config_dir"
[[ -z "$(find "$osmo_config_dir" -mindepth 1 -print -quit)" ]] || \
  fatal "Isolated OSMO profile directory must be empty before fresh code login"
osmo login "$service_url" --method code
version_json=$(curl --fail --silent --show-error --connect-timeout 5 "${service_url%/}/api/version")
jq -e --arg major "$service_major" --arg minor "$service_minor" '
  ((.major // "") | tostring) == $major and ((.minor // "") | tostring) == $minor
' <<< "$version_json" >/dev/null || fatal "OSMO service version does not match the catalog contract"
backend_json=$(osmo config show BACKEND "$backend_name")
pool_json=$(osmo config show POOL "$pool_name")
jq -e --arg backend "$backend_name" '
  (.name == $backend) or any(.backends[]?; .name == $backend)
' <<< "$backend_json" >/dev/null || fatal "OSMO backend response does not identify $backend_name"
jq -e --arg pool "$pool_name" --arg backend "$backend_name" '
  ((.name == $pool) and ((.backend // $backend) == $backend)) or
  any(.pools[]?; .name == $pool and ((.backend // $backend) == $backend))
' <<< "$pool_json" >/dev/null || fatal "OSMO pool response does not identify the expected backend relationship"
osmo profile set pool "$pool_name" >/dev/null

# Keep downloaded charts and rendered manifests in a private temporary directory that is removed on exit.
work_dir=$(mktemp -d)
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT
chmod 0700 "$work_dir"

# Pull both charts and verify their content against the catalog's pinned references, versions, and digests.
operation="pull and verify local scheduler and backend charts"
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

# Render and install KAI Scheduler with architecture-specific immutable images, then verify its running digests.
operation="install KAI Scheduler on local K3s"
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
kube_helm "$kubeconfig" "$context" template kai-scheduler "$kai_chart" \
  "${kai_helm_args[@]}" > "$work_dir/rendered-kai.yaml"
grep -Eq '^[[:space:]]*privileged:[[:space:]]*true' "$work_dir/rendered-kai.yaml" && \
  fatal "Rendered KAI requests privileged containers"
grep -Eq '^[[:space:]]*host(Network|PID|IPC):[[:space:]]*true' "$work_dir/rendered-kai.yaml" && \
  fatal "Rendered KAI requests host namespace access"
grep -Eq '^[[:space:]]*hostPath:' "$work_dir/rendered-kai.yaml" && fatal "Rendered KAI requests hostPath access"
grep -Eq '^[[:space:]]*name:[[:space:]]*cluster-admin[[:space:]]*$' "$work_dir/rendered-kai.yaml" && \
  fatal "Rendered KAI binds cluster-admin"
grep -Eq '^[[:space:]]*(verbs|resources|apiGroups):[[:space:]]*\[[^]]*"\*"' "$work_dir/rendered-kai.yaml" && \
  fatal "Rendered KAI contains inline wildcard RBAC permissions"
grep -Eq "^[[:space:]]*-[[:space:]]*['\"]?\\*['\"]?[[:space:]]*$" "$work_dir/rendered-kai.yaml" && \
  fatal "Rendered KAI contains block-style wildcard RBAC permissions"
kube_helm "$kubeconfig" "$context" upgrade --install kai-scheduler "$kai_chart" \
  --namespace "$NS_KAI_SCHEDULER" --create-namespace \
  "${kai_helm_args[@]}" \
  --wait --timeout "$TIMEOUT_DEPLOY"
kai_pods=$(kube_kubectl "$kubeconfig" "$context" get pods -n "$NS_KAI_SCHEDULER" -o json)
jq -e '
  [.items[].spec.containers[], .items[].spec.initContainers[]?] as $declared |
  [.items[].status.containerStatuses[]?, .items[].status.initContainerStatuses[]?] as $running |
  ($declared | length) > 0 and ($running | length) > 0 and
  all($declared[]; .image | test("@sha256:[0-9a-f]{64}$")) and
  all($running[];
    (.image | split("@")[-1]) as $digest |
    (.imageID // "") | endswith("@" + $digest))
' <<< "$kai_pods" >/dev/null || fatal "KAI workloads are not running their declared immutable image digests"

# Create only the namespaces and pull secrets needed by the local operator and workflow workloads.
operation="prepare local backend namespaces and protected inputs"
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

# Render the backend first and reject privileged containers, host access, wildcard permissions, and cluster-wide RBAC.
operation="render and inspect the local backend"
kube_helm "$kubeconfig" "$context" template osmo-hil-operator "$backend_chart" \
  "${helm_args[@]}" > "$work_dir/rendered-backend.yaml"
grep -Eq '^[[:space:]]*privileged:[[:space:]]*true' "$work_dir/rendered-backend.yaml" && \
  fatal "Rendered backend requests privileged containers"
grep -Eq '^[[:space:]]*host(Network|PID|IPC):[[:space:]]*true' "$work_dir/rendered-backend.yaml" && \
  fatal "Rendered backend requests host namespace access"
grep -Eq '^[[:space:]]*hostPath:' "$work_dir/rendered-backend.yaml" && \
  fatal "Rendered backend requests hostPath access"
grep -Eq '^[[:space:]]*name:[[:space:]]*cluster-admin[[:space:]]*$' "$work_dir/rendered-backend.yaml" && \
  fatal "Rendered backend binds cluster-admin"
grep -Eq '^[[:space:]]*(verbs|resources|apiGroups):[[:space:]]*\[[^]]*"\*"' \
  "$work_dir/rendered-backend.yaml" && fatal "Rendered backend contains wildcard RBAC permissions"
grep -Eq "^[[:space:]]*-[[:space:]]*['\"]?\\*['\"]?[[:space:]]*$" \
  "$work_dir/rendered-backend.yaml" && fatal "Rendered backend contains block-style wildcard RBAC permissions"
grep -Eq '^kind:[[:space:]]+ClusterRole(Binding)?[[:space:]]*$' "$work_dir/rendered-backend.yaml" && \
  fatal "Rendered backend contains cluster-scoped RBAC"

# Install or update the backend with the approved values and wait for Helm's deployment-level readiness checks.
operation="deploy the local backend"
kube_helm "$kubeconfig" "$context" upgrade --install osmo-hil-operator "$backend_chart" \
  "${helm_args[@]}" --wait --timeout "$TIMEOUT_DEPLOY"

# Confirm both backend deployments roll out and that every running image matches the approved immutable manifest.
operation="verify local backend rollout"
kube_kubectl "$kubeconfig" "$context" rollout status deployment/osmo-hil-operator-osmo-backend-listener \
  -n "$operator_namespace" --timeout "$TIMEOUT_DEPLOY"
kube_kubectl "$kubeconfig" "$context" rollout status deployment/osmo-hil-operator-osmo-backend-worker \
  -n "$operator_namespace" --timeout "$TIMEOUT_DEPLOY"
backend_pods=$(kube_kubectl "$kubeconfig" "$context" get pods -n "$operator_namespace" -o json)
jq -e --slurpfile manifest "$image_manifest" '
  ["backend-listener", "backend-worker", "client", "init-container"] as $allowed_components |
  all(.items[].spec.containers[], .items[].spec.initContainers[]?;
    (.image | split("/")[-1] | split(":")[0]) as $component |
    ($allowed_components | index($component)) != null and
    (.image | startswith($manifest[0].login_server + "/osmo/" + $component + ":" + $manifest[0].image_version))) and
  [.items[].status.containerStatuses[]?, .items[].status.initContainerStatuses[]?] as $statuses |
  ($statuses | length) > 0 and
  all($statuses[];
    (.image | split("/")[-1] | split(":")[0]) as $component |
    ($manifest[0].images[$component].digest // "") as $digest |
    $digest != "" and ((.imageID // "") | endswith("@" + $digest)))
' <<< "$backend_pods" >/dev/null || fatal "Declared or running backend images do not match the approved immutable set"
# Inspect effective permissions for every service account and reject secret access, escalation, and unrestricted scope.
for namespace in "$operator_namespace" "$workflow_namespace" "$NS_KAI_SCHEDULER"; do
  mapfile -t service_accounts < <(kube_kubectl "$kubeconfig" "$context" get serviceaccounts \
    -n "$namespace" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  for service_account in "${service_accounts[@]}"; do
    [[ -n "$service_account" ]] || continue
    principal="system:serviceaccount:${namespace}:${service_account}"
    permissions_file="$work_dir/permissions-${namespace}-${service_account}.txt"
    kube_kubectl "$kubeconfig" "$context" auth can-i --list --no-headers \
      --as "$principal" --namespace "$namespace" > "$permissions_file"
    if awk '
      {
        resources=$1; verbs="";
        for (i=4; i<=NF; i++) verbs=verbs " " $i;
        if (resources ~ /(^|,)secrets(,|$)/) exit 1;
        if (resources ~ /(^|,)(clusterroles|clusterrolebindings|roles|rolebindings)(,|$)/ && verbs ~ / (create|update|patch|delete|deletecollection|impersonate)( |\])/ ) exit 1;
        if (resources ~ /(^|,)(pods\/exec|pods\/attach|pods\/portforward)(,|$)/) exit 1;
        if (resources == "*" || verbs ~ / \*( |$)/) exit 1;
      }
    ' "$permissions_file"; then
      :
    else
      fatal "ServiceAccount ${namespace}/${service_account} exceeds the effective permission policy"
    fi
    [[ "$(kube_kubectl "$kubeconfig" "$context" auth can-i '*' '*' --as "$principal" --all-namespaces)" != "yes" ]] || \
      fatal "ServiceAccount ${namespace}/${service_account} has unrestricted cluster permissions"
    [[ "$(kube_kubectl "$kubeconfig" "$context" auth can-i list secrets --as "$principal" --all-namespaces)" != "yes" ]] || \
      fatal "ServiceAccount ${namespace}/${service_account} can list secrets cluster-wide"
    [[ "$(kube_kubectl "$kubeconfig" "$context" auth can-i create pods --as "$principal" --all-namespaces)" != "yes" ]] || \
      fatal "ServiceAccount ${namespace}/${service_account} can create Pods cluster-wide"
    [[ "$(kube_kubectl "$kubeconfig" "$context" auth can-i create clusterrolebindings.rbac.authorization.k8s.io --as "$principal")" != "yes" ]] || \
      fatal "ServiceAccount ${namespace}/${service_account} can create ClusterRoleBindings"
  done
done

# Wait until the existing remote OSMO backend reports that it is online and ready to receive workflows.
operation="verify existing OSMO backend is online"
backend_online=false
for ((attempt = 1; attempt <= 60; attempt++)); do
  backend_json=$(osmo config show BACKEND "$backend_name")
  if jq -e --arg backend "$backend_name" '
      (.online // false) == true or any(.backends[]?; .name == $backend and .online == true)
    ' <<< "$backend_json" >/dev/null 2>&1; then
    backend_online=true
    break
  fi
  (( attempt < 60 )) || break
  sleep 5
done
[[ "$backend_online" == "true" ]] || fatal "OSMO backend $backend_name did not report online"

# Record the successful, non-secret connection details so later smoke checks can verify the same target.
operation="record successful non-secret connection"
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

# Summarize the connected target and identify the next local validation command.
trap - ERR
section "Deployment Summary"
print_kv "Milestone" "connected"
print_kv "Environment" "$environment"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Service URL" "$service_url"
print_kv "K3s Context" "$context"
print_kv "Backend Status" "online"
print_kv "Connection Receipt" "$connection_file"
print_kv "Next" "$SCRIPT_DIR/03-run-cpu-smoke.sh --connection-file $connection_file"
info "Local compute plane is connected to OSMO"
