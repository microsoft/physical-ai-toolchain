#!/usr/bin/env bash
# Build and deploy the dataviewer application to Azure Container Apps
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Build and deploy the dataviewer application to Azure Container Apps.

OPTIONS:
    -h, --help               Show this help message
    -t, --tf-dir DIR         Terraform directory (default: $DEFAULT_TF_DIR)
    --tag TAG                Image tag (default: auto-generated from git SHA)
    --skip-build             Skip container image builds (use existing images)
    --skip-update            Skip container app update (build images only)
    --skip-backend           Skip backend build, deploy, and auth settings
    --skip-frontend          Skip frontend build, deploy, and Easy Auth settings
    --local-build            Build with local Docker BuildKit and push to ACR
                             instead of ACR quick builds
    --config-preview         Print configuration and exit

When building images, the tag defaults to 'sha-<git-short-hash>' for unique
revisions. Use --tag to override, or --skip-build to reference existing images.

Backend updates also set STORAGE_BACKEND=azure and remove the legacy
HMI_STORAGE_BACKEND variable, so a new image never falls back to local storage.

ACR quick builds use Docker's classic builder, which rejects BuildKit syntax
such as RUN --mount and HEALTHCHECK --start-interval. The frontend Dockerfile
uses both, so build it with --local-build (Docker with buildx, platform
\$DATAVIEWER_BUILD_PLATFORM, default linux/amd64).

Backend images install the comma-separated extras in \$DATAVIEWER_BACKEND_EXTRAS
(default: azure,analysis,export,auth). The yolo extra adds CUDA torch and makes
the image larger than the 8 GB that Container Apps' Consumption profile supports,
so object detection returns 503 on Container Apps.

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --tag v0.1.0
    $(basename "$0") --skip-build
    $(basename "$0") --skip-frontend --tag sha-abc1234
    $(basename "$0") --local-build --skip-backend
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
image_tag="$DATAVIEWER_IMAGE_TAG"
tag_explicit=false
skip_build=false
skip_update=false
skip_backend=false
skip_frontend=false
local_build=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -t|--tf-dir)         tf_dir="$2"; shift 2 ;;
    --tag)               image_tag="$2"; tag_explicit=true; shift 2 ;;
    --skip-build)        skip_build=true; shift ;;
    --skip-update)       skip_update=true; shift ;;
    --skip-backend)      skip_backend=true; shift ;;
    --skip-frontend)     skip_frontend=true; shift ;;
    --local-build)       local_build=true; shift ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform jq curl base64
[[ "$local_build" == "true" && "$skip_build" == "false" ]] && require_tools docker

easy_auth_secret_name="microsoft-provider-authentication-secret"

wait_for_ready_revision() {
  local app="$1"
  local expected_revision="$2"
  local ready_revision

  for _ in {1..60}; do
    ready_revision=$(az containerapp show \
      --name "$app" \
      --resource-group "$rg" \
      --query properties.latestReadyRevisionName -o tsv)
    if [[ -n "$expected_revision" && "$ready_revision" == "$expected_revision" ]]; then
      return 0
    fi
    sleep 5
  done

  error "Timed out waiting for revision $expected_revision on $app"
  return 1
}

update_app_and_wait() {
  local app="$1"
  shift
  local revision

  revision=$(az containerapp update \
    --name "$app" \
    --resource-group "$rg" \
    "$@" \
    --query properties.latestRevisionName \
    --output tsv) || return 1
  if [[ -z "$revision" ]]; then
    error "Container App update returned no revision for $app"
    return 1
  fi
  wait_for_ready_revision "$app" "$revision" || return 1
  printf '%s\n' "$revision"
}

verify_backend_auth_enforced() {
  local app_state

  app_state=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --output json) || return 1
  jq -e \
    --argjson caller "${caller_auth:-null}" \
    '
      [.properties.template.containers[]? | select(.name == "backend")] as $containers
      | ($containers[0].env // []) as $settings
      | ($settings | map({key: .name, value: .value}) | from_entries) as $env
      | ($containers | length) == 1
      and ($caller | type == "object")
      and ([$caller.tenant_id, $caller.api_client_id, $caller.caller_object_id, $caller.caller_client_id]
        | all(.[]; type == "string" and length > 0))
      and ([$settings[].name] | length == (unique | length))
      and ([$settings[] | select(.name == "DATAVIEWER_PROXY_KEY")] | length == 0)
      and $env.DATAVIEWER_AUTH_PROVIDER == "easy_auth"
      and $env.DATAVIEWER_AUTH_DISABLED == "false"
      and $env.DATAVIEWER_AZURE_TENANT_ID == $caller.tenant_id
      and $env.DATAVIEWER_AZURE_CLIENT_ID == $caller.api_client_id
      and $env.DATAVIEWER_CALLER_OBJECT_ID == $caller.caller_object_id
      and $env.DATAVIEWER_CALLER_CLIENT_ID == $caller.caller_client_id
    ' <<<"$app_state" >/dev/null
}

verify_frontend_auth_enforced() {
  local auth_state

  auth_state=$(az containerapp auth show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --output json) || return 1
  jq -e \
    '
      .platform.enabled == true
      and .globalValidation.unauthenticatedClientAction == "RedirectToLoginPage"
    ' <<<"$auth_state" >/dev/null
}

validate_caller_contract() {
  jq -e '
    def uuid: type == "string" and test("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
    type == "object" and length == 6 and .mode == "managed_identity"
    and ([.tenant_id, .api_client_id, .caller_object_id, .caller_client_id] | all(.[]; uuid))
    and (.caller_identity_id | type == "string" and test("^/subscriptions/[0-9a-f-]+/resourceGroups/[^/]+/providers/Microsoft.ManagedIdentity/userAssignedIdentities/[^/]+$"; "i"))
  ' <<<"$1" >/dev/null
}

verify_snapshot_ready() {
  jq -e '
    .properties.latestRevisionName as $revision
    | ($revision | type == "string" and length > 0)
    and .properties.latestReadyRevisionName == $revision
    and all(.properties.template.containers[].image;
      type == "string" and test("@sha256:[0-9a-f]{64}$"))
  ' <<<"$1" >/dev/null
}

pin_registry_image() {
  local image="$1" relative digest repository

  [[ "$image" == "$acr_login_server/"* ]] || return 1
  relative="${image#"$acr_login_server/"}"
  digest=$(az acr repository show --name "$acr_name" --image "$relative" --query digest -o tsv) || return 1
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || return 1
  repository="${relative%%@*}"
  repository="${repository%%:*}"
  printf '%s/%s@%s\n' "$acr_login_server" "$repository" "$digest"
}

build_app_manifest() {
  local state="$1" role="$2" image="$3" mode="$4" helper_image="$5"

  jq -e \
    --arg role "$role" --arg image "$image" --arg mode "$mode" \
    --arg helper_image "$helper_image" --arg identity "$identity_id" \
    --arg storage_client_id "$storage_client_id" \
    --arg backend_host "$backend_fqdn" --argjson caller "${caller_auth:-null}" '
    def setting($name; $value): {name: $name, value: $value};
    def probe($type; $path; $timeout; $port): {
      type: $type, httpGet: {path: $path, port: $port, scheme: "HTTP"},
      timeoutSeconds: $timeout, periodSeconds: 30, failureThreshold: 3
    };
    [.properties.template.containers[] | select(.name == $role)] as $selected
    | if ($selected | length) != 1 then error("Expected one named application container") else . end
    | if any($selected[0].env[]?;
        (.name | test("(^|_)(API_KEY|PASSWORD|ACCESS_TOKEN|CLIENT_SECRET|CONNECTION_STRING)$"))
        and ((.value // "") | length > 0))
      then error("Credential settings must use Container App secret references") else . end
    | .properties.template.containers = [
        ($selected[0]
          | .image = $image
          | .env = [(.env // [])[] | select(
              (.name | startswith("DATAVIEWER_CALLER_") | not)
              and (.name != "DATAVIEWER_PROXY_KEY")
              and (.name != "DATAVIEWER_AZURE_TENANT_ID")
              and (.name != "DATAVIEWER_AZURE_CLIENT_ID")
              and (.name != "DATAVIEWER_AUTH_DISABLED")
              and (.name != "DATAVIEWER_AUTH_PROVIDER")
              and (.name != "HMI_STORAGE_BACKEND")
            )]
          | if $role == "backend" then
                .env = [.env[] | select(.name != "STORAGE_BACKEND" and .name != "AZURE_CLIENT_ID")]
                + [setting("STORAGE_BACKEND"; "azure"), setting("AZURE_CLIENT_ID"; $storage_client_id),
                  setting("DATAVIEWER_AUTH_DISABLED"; if $mode == "off" then "true" else "false" end)]
              + (if $mode == "off" then [] else [
                  setting("DATAVIEWER_AUTH_PROVIDER"; "easy_auth"),
                  setting("DATAVIEWER_AZURE_TENANT_ID"; $caller.tenant_id),
                  setting("DATAVIEWER_AZURE_CLIENT_ID"; $caller.api_client_id),
                  setting("DATAVIEWER_CALLER_OBJECT_ID"; $caller.caller_object_id),
                  setting("DATAVIEWER_CALLER_CLIENT_ID"; $caller.caller_client_id)
                ] end)
              | .command = ["sh", "-c", "exec uvicorn src.api.main:app --log-config logging.json --log-level \"${LOG_LEVEL:-info}\" --host 0.0.0.0 --port 8000"]
              | .args = []
              | .probes = [probe("Liveness"; "/health"; 2; 8000), probe("Readiness"; "/health"; 10; 8000)]
            else
              .env = [.env[] | select(.name != "NGINX_BACKEND_HOST" and .name != "NGINX_BACKEND_SCHEME")]
                + [setting("DATAVIEWER_CALLER_MODE"; $mode), setting("NGINX_BACKEND_HOST"; $backend_host), setting("NGINX_BACKEND_SCHEME"; "https")]
              | .command = ["/bin/sh", "/usr/local/bin/start-nginx.sh"]
              | .args = []
              | .probes = [probe("Liveness"; "/"; 2; 8080), probe("Readiness"; "/health"; 10; 8080)]
            end
        )
      ] + (if $role == "frontend" and $mode != "off" then [{
        name: "caller-helper", image: $helper_image,
        command: ["python", "-m", "src.caller_auth_helper"], args: [],
        resources: {cpu: 0.25, memory: "0.5Gi"}, probes: [], volumeMounts: [],
        env: [setting("DATAVIEWER_AZURE_CLIENT_ID"; $caller.api_client_id), setting("DATAVIEWER_CALLER_CLIENT_ID"; $caller.caller_client_id)]
      }] else [] end)
    | .identity = {type: "UserAssigned", userAssignedIdentities: (
        {($identity): {}} + (if $role == "frontend" and $mode != "off" then {($caller.caller_identity_id): {}} else {} end)
      )}
    | del(.id, .systemData, .properties.provisioningState, .properties.runningStatus,
        .properties.latestRevisionName, .properties.latestReadyRevisionName,
        .properties.latestRevisionFqdn, .properties.outboundIpAddresses,
        .properties.eventStreamEndpoint, .properties.configuration.secrets)
  ' <<<"$state"
}

verify_manifest_rollout() {
  local app="$1" revision="$2" manifest="$3" actual

  actual=$(az containerapp show --name "$app" --resource-group "$rg" --output json) || return 1
  jq -e --arg revision "$revision" --argjson desired "$manifest" '
    def env: (.env // [] | map({name, value: (.value // null), secretRef: (.secretRef // null)}) | sort_by(.name));
    def probes: (.probes // [] | map({
      type, timeoutSeconds, periodSeconds, failureThreshold,
      httpGet: (if .httpGet then {
        path: .httpGet.path, port: .httpGet.port, scheme: (.httpGet.scheme // "HTTP"),
        host: (.httpGet.host // null), httpHeaders: (.httpGet.httpHeaders // [])
      } else null end)
    }) | sort_by(.type));
    . as $actual
    | .properties.latestRevisionName == $revision and .properties.latestReadyRevisionName == $revision
    and ((.identity.userAssignedIdentities | keys | map(ascii_downcase) | sort)
      == ($desired.identity.userAssignedIdentities | keys | map(ascii_downcase) | sort))
    and (.properties.template.containers | length) == ($desired.properties.template.containers | length)
    and all($desired.properties.template.containers[];
      . as $expected
      | [$actual.properties.template.containers[] | select(.name == $expected.name)] as $containers
      | ($containers | length) == 1
      and ($containers[0] | .image == $expected.image
        and (.command // []) == ($expected.command // [])
        and (.args // []) == ($expected.args // [])
        and (.volumeMounts // []) == ($expected.volumeMounts // [])
        and .resources.cpu == $expected.resources.cpu and .resources.memory == $expected.resources.memory
        and env == ($expected | env) and probes == ($expected | probes)))
  ' <<<"$actual" >/dev/null
}

apply_app_manifest() {
  local app="$1" manifest="$2" manifest_file revision status=0

  manifest_file=$(mktemp) || return 1
  chmod 600 "$manifest_file" || { rm -f -- "$manifest_file"; return 1; }
  printf '%s\n' "$manifest" >"$manifest_file"
  revision=$(update_app_and_wait "$app" --yaml "$manifest_file") || status=1
  rm -f -- "$manifest_file"
  if [[ "$status" != "0" ]]; then
    return 1
  fi
  verify_manifest_rollout "$app" "$revision" "$manifest" || return 1
  printf '%s\n' "$revision"
}

verify_anonymous_request_denied() {
  local forged_principal status

  forged_principal=$(printf '%s' \
    '{"auth_typ":"aad","claims":[{"typ":"http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier","val":"forged-subject"}]}' \
    | base64 | tr -d '\n')
  status=$(curl \
    --silent \
    --show-error \
    --output /dev/null \
    --write-out '%{http_code}' \
    --max-time 30 \
    --header "X-MS-CLIENT-PRINCIPAL: ${forged_principal}" \
    "${frontend_url}/api/auth/context") || return 1
  case "$status" in
    302|401|403) return 0 ;;
    *)
      error "Anonymous forged-principal probe returned HTTP $status"
      return 1
      ;;
  esac
}

close_frontend_ingress() {
  local ingress_external

  warn "Closing frontend ingress because no verified secure recovery pair is available."
  az containerapp ingress disable \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --output none || return 1
  ingress_external=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query properties.configuration.ingress.external \
    --output tsv) || return 1
  [[ "$ingress_external" != "true" ]]
}

ensure_frontend_ingress() {
  local ingress_external

  ingress_external=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query properties.configuration.ingress.external \
    --output tsv) || return 1
  if [[ "$ingress_external" == "true" ]]; then
    return 0
  fi

  az containerapp ingress enable \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --target-port 8080 \
    --transport auto \
    --type external \
    --output none || return 1
  ingress_external=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query properties.configuration.ingress.external \
    --output tsv) || return 1
  [[ "$ingress_external" == "true" ]]
}

operator_auth_gate() {
  local confirmation

  info "Authentication gate: managed-identity caller and Easy Auth rollout"
  info "Open $frontend_url in a private browser window and sign in."
  info "Confirm /api/auth/context returns HTTP 200 with auth_mode=easy_auth."
  info "Confirm image and frame loads plus video playback, HEAD, and Range requests work without a browser Authorization header."
  if ! IFS= read -r -t 900 -p "Type 'yes' within 15 minutes to confirm this gate, or anything else to roll back: " confirmation; then
    error "Authentication gate timed out or input ended before confirmation"
    return 1
  fi
  [[ "$confirmation" == "yes" ]]
}

restore_app_rollout() {
  apply_app_manifest "$1" "$2" >/dev/null
}

rollback_backend() {
  restore_app_rollout "$backend_app" "$old_backend_manifest" || return 1
  verify_backend_auth_enforced
}

recover_authenticated_rollout() {
  local recovery_failed=false

  if [[ "$old_rollout_secure" != "true" ]]; then
    close_frontend_ingress
    return
  fi

  if [[ "$skip_backend" == "false" ]]; then
    rollback_backend || recovery_failed=true
  fi
  if [[ "$skip_frontend" == "false" ]]; then
    restore_app_rollout \
      "$frontend_app" \
      "$old_frontend_pending_manifest" || recovery_failed=true
    verify_frontend_auth_enforced || recovery_failed=true
    verify_anonymous_request_denied || recovery_failed=true
  fi

  if [[ "$recovery_failed" == "false" && "$skip_frontend" == "false" ]]; then
    verify_backend_auth_enforced || recovery_failed=true
    if [[ "$recovery_failed" == "false" ]]; then
      restore_app_rollout "$frontend_app" "$old_frontend_manifest" || recovery_failed=true
      verify_frontend_auth_enforced || recovery_failed=true
      verify_anonymous_request_denied || recovery_failed=true
    fi
  fi

  if [[ "$recovery_failed" == "true" ]]; then
    close_frontend_ingress || return 1
    return 1
  fi
}

configure_frontend_auth() {
  az containerapp auth microsoft update \
    --name "$frontend_app" --resource-group "$rg" \
    --client-id "$entra_client_id" --client-secret-name "$easy_auth_secret_name" \
    --issuer "https://login.microsoftonline.com/${entra_tenant_id}/v2.0" \
    --yes --output none || return 1
  az containerapp auth update \
    --name "$frontend_app" --resource-group "$rg" --enabled true \
    --unauthenticated-client-action RedirectToLoginPage \
    --redirect-provider azureactivedirectory --output none || return 1
  verify_frontend_auth_enforced
}

verify_frontend_runtime() {
  jq -e --argjson caller "$caller_auth" --arg helper_image "$2" '
    [.properties.template.containers[]? | select(.name == "frontend")] as $frontends
    | [.properties.template.containers[]? | select(.name == "caller-helper")] as $helpers
    | ($frontends | length) == 1 and ($helpers | length) == 1
    and (.properties.template.containers | length) == 2
    and (.identity.userAssignedIdentities | with_entries(.key |= ascii_downcase) | has($caller.caller_identity_id | ascii_downcase))
    and ([$frontends[0].env[]? | select(.name == "DATAVIEWER_CALLER_MODE") | .value] == ["managed_identity"])
    and ([.properties.template.containers[].env[]? | select(.name == "DATAVIEWER_PROXY_KEY")] | length == 0)
    and $helpers[0].image == $helper_image
    and $helpers[0].command == ["python", "-m", "src.caller_auth_helper"]
    and (($helpers[0].probes // []) | length == 0)
    and (($helpers[0].volumeMounts // []) | length == 0)
    and ($helpers[0].env | length == 2)
    and ([$helpers[0].env[] | select(.name == "DATAVIEWER_AZURE_CLIENT_ID") | .value] == [$caller.api_client_id])
    and ([$helpers[0].env[] | select(.name == "DATAVIEWER_CALLER_CLIENT_ID") | .value] == [$caller.caller_client_id])
  ' <<<"$1" >/dev/null
}

deploy_authenticated_pair() {
  if [[ "$skip_frontend" == "false" ]]; then
    close_frontend_ingress || return 1
    configure_frontend_auth || return 1
  fi

  if [[ "$skip_backend" == "false" ]]; then
    apply_app_manifest "$backend_app" "$backend_manifest" >/dev/null || return 1
  fi
  verify_backend_auth_enforced || return 1

  if [[ "$skip_frontend" == "false" ]]; then
    apply_app_manifest "$frontend_app" "$frontend_pending_manifest" >/dev/null || return 1
    verify_frontend_auth_enforced || return 1
    ensure_frontend_ingress || return 1
    verify_frontend_auth_enforced || return 1
    verify_anonymous_request_denied || return 1
    apply_app_manifest "$frontend_app" "$frontend_manifest" >/dev/null || return 1
  fi
  verify_frontend_auth_enforced || return 1
  verify_anonymous_request_denied || return 1
  operator_auth_gate
}

rollback_failed() {
  error "Automatic secure recovery failed. Keep frontend ingress closed while repairing:"
  error "Frontend: $frontend_app"
  error "Backend: $backend_app"
  exit 1
}

SRC_DIR="$SCRIPT_DIR/../viewer"
backend_dockerfile="$SRC_DIR/backend/Dockerfile"
frontend_dockerfile="$REPO_ROOT/data-management/viewer/frontend/Dockerfile"

# ACR quick builds use the classic builder; detect Dockerfiles that need BuildKit.
requires_buildkit() {
  grep -Eq '^[[:space:]]*RUN[[:space:]]+--mount=|--start-interval=' "$1"
}

if [[ "$local_build" == "true" ]]; then
  build_mode="local Docker BuildKit ($DATAVIEWER_BUILD_PLATFORM)"
else
  build_mode="ACR quick build"
fi

# ACR Tasks passes build arguments to docker build unquoted, so the extras list
# is normalized to commas with no spaces.
backend_extras=$(printf '%s' "$DATAVIEWER_BACKEND_EXTRAS" | tr -s ', ' ',,' | sed -e 's/^,//' -e 's/,$//')
judge_extras=$(printf '%s' "$DATAVIEWER_JUDGE_EXTRAS" | tr -s ', ' ',,' | sed -e 's/^,//' -e 's/,$//')

buildkit_images=()
if [[ "$skip_build" == "false" ]]; then
  if [[ "$skip_backend" == "false" ]] && requires_buildkit "$backend_dockerfile"; then
    buildkit_images+=(backend)
  fi
  if [[ "$skip_frontend" == "false" ]] && requires_buildkit "$frontend_dockerfile"; then
    buildkit_images+=(frontend)
  fi
fi

# Auto-generate a unique image tag when building and no explicit --tag provided.
# Uses git short SHA for traceability; falls back to timestamp outside a git repo.
if [[ "$tag_explicit" == "false" && "$skip_build" == "false" ]]; then
  if git_sha=$(git rev-parse --short HEAD 2>/dev/null); then
    image_tag="sha-${git_sha}"
  else
    image_tag="build-$(date -u +%Y%m%d%H%M%S)"
  fi
fi

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
acr_name=$(tf_require "$tf_output" "container_registry.value.name" "ACR name")
acr_login_server=$(tf_require "$tf_output" "container_registry.value.login_server" "ACR login server")

# Verify dataviewer is deployed
dataviewer_deployed=$(tf_get "$tf_output" "dataviewer.value" "")
if [[ -z "$dataviewer_deployed" || "$dataviewer_deployed" == "null" ]]; then
  fatal "Dataviewer is not deployed. Set should_deploy_dataviewer=true in terraform.tfvars and run terraform apply first."
fi

backend_app=$(tf_require "$tf_output" "dataviewer.value.backend.name" "Backend container app name")
frontend_app=$(tf_require "$tf_output" "dataviewer.value.frontend.name" "Frontend container app name")
identity_id=$(tf_require "$tf_output" "dataviewer.value.identity.id" "Managed identity resource ID")
storage_client_id=$(tf_require "$tf_output" "dataviewer.value.identity.client_id" "Storage identity client ID")
frontend_url=$(tf_get "$tf_output" "dataviewer.value.frontend.url" "")

# Entra ID auth configuration (empty when should_deploy_auth=false)
entra_client_id=$(tf_get "$tf_output" "dataviewer.value.entra_id.client_id" "")
entra_tenant_id=$(tf_get "$tf_output" "dataviewer.value.entra_id.tenant_id" "")
caller_auth=$(tf_get "$tf_output" "dataviewer.value.caller_auth" "null")
backend_fqdn=$(tf_get "$tf_output" "dataviewer.value.backend.fqdn" "")
auth_enabled=false
if [[ -n "$entra_client_id" && "$entra_client_id" != "null" ]]; then
  auth_enabled=true
  validate_caller_contract "$caller_auth" || fatal "Apply the managed-identity caller Terraform contract before deployment."
  if [[ "$(jq -r '.api_client_id' <<<"$caller_auth")" != "$entra_client_id" ||
    "$(jq -r '.tenant_id' <<<"$caller_auth")" != "$entra_tenant_id" ]]; then
    fatal "Caller and Easy Auth tenant/API contracts must match."
  fi
else
  caller_auth=null
fi

backend_image="${acr_login_server}/${DATAVIEWER_BACKEND_IMAGE}:${image_tag}"
frontend_image="${acr_login_server}/${DATAVIEWER_FRONTEND_IMAGE}:${image_tag}"

# Terraform ignores the container template after creation, so backend rollouts
# reconcile the storage setting; current images default to local storage without it.
backend_storage_env="STORAGE_BACKEND=azure"
legacy_backend_storage_env="HMI_STORAGE_BACKEND"
if [[ "$skip_update" == "true" || "$skip_backend" == "true" ]]; then
  backend_storage_status="Unchanged"
else
  backend_storage_status="$backend_storage_env (removes $legacy_backend_storage_env)"
fi

if [[ "$skip_update" == "true" || "$skip_frontend" == "true" ]]; then
  easy_auth_status="Unchanged"
elif [[ "$auth_enabled" == "true" ]]; then
  easy_auth_status="Configured"
else
  easy_auth_status="Disabled"
fi

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

section "Configuration"
print_kv "Resource Group" "$rg"
print_kv "ACR" "$acr_name"
print_kv "Image Tag" "$image_tag"
if [[ "$tag_explicit" == "true" ]]; then
  tag_source="explicit (--tag)"
elif [[ "$skip_build" == "true" ]]; then
  tag_source="default (skip-build)"
else
  tag_source="auto-generated"
fi
print_kv "Tag Source" "$tag_source"
print_kv "Backend Image" "$backend_image"
print_kv "Frontend Image" "$frontend_image"
print_kv "Backend App" "$backend_app"
print_kv "Frontend App" "$frontend_app"
print_kv "Identity" "${identity_id##*/}"
print_kv "Skip Build" "$skip_build"
print_kv "Skip Update" "$skip_update"
print_kv "Auth Enabled" "$auth_enabled"
print_kv "Easy Auth" "$easy_auth_status"
print_kv "Backend Storage" "$backend_storage_status"
print_kv "Build Mode" "$([[ "$skip_build" == "true" ]] && echo 'Skipped' || echo "$build_mode")"
if [[ "$skip_build" == "false" ]]; then
  print_kv "Needs BuildKit" "$([[ ${#buildkit_images[@]} -gt 0 ]] && echo "${buildkit_images[*]}" || echo 'none')"
  if [[ "$skip_backend" == "false" ]]; then
    print_kv "Backend Extras" "${backend_extras:-none}"
    if [[ ",$backend_extras," == *",yolo,"* ]]; then
      warn "The yolo extra makes the backend image larger than Container Apps' Consumption profile supports (8 GB)."
    fi
  fi
fi

if [[ "$config_preview" == "true" ]]; then
  info "Config preview mode — exiting without changes."
  exit 0
fi

if [[ "$skip_backend" == "true" && "$skip_frontend" == "true" ]]; then
  info "No application selected; exiting without changes."
  exit 0
fi

if [[ "$skip_build" == "false" && "$local_build" == "false" && ${#buildkit_images[@]} -gt 0 ]]; then
  fatal "ACR quick builds can't build the ${buildkit_images[*]} Dockerfile (BuildKit syntax). Rerun with --local-build, or skip that image."
fi

#------------------------------------------------------------------------------
# Capture Rollback State and Validate Authentication Rollout
#------------------------------------------------------------------------------

old_rollout_secure=false
old_backend_manifest=""
old_frontend_pending_manifest=""
old_frontend_manifest=""
backend_manifest=""
frontend_pending_manifest=""
frontend_manifest=""
helper_image="$backend_image"

if [[ "$skip_update" == "false" ]]; then
  backend_state=$(az containerapp show --name "$backend_app" --resource-group "$rg" --output json)
  frontend_state=$(az containerapp show --name "$frontend_app" --resource-group "$rg" --output json)
  old_backend_image=$(jq -er '.properties.template.containers | map(select(.name == "backend")) | if length == 1 then .[0].image else error("Expected one backend") end' <<<"$backend_state")
  old_frontend_image=$(jq -er '.properties.template.containers | map(select(.name == "frontend")) | if length == 1 then .[0].image else error("Expected one frontend") end' <<<"$frontend_state")
  old_helper_image=$(jq -r '.properties.template.containers | map(select(.name == "caller-helper")) | if length == 1 then .[0].image else "" end' <<<"$frontend_state")

  if [[ "$auth_enabled" == "true" ]]; then
    if [[ ! -t 0 || ! -t 1 ]]; then
      fatal "Authenticated rollout requires an interactive terminal for the final browser validation gate."
    fi

    frontend_easy_auth_secret=$(az containerapp secret list \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --query "[?name=='${easy_auth_secret_name}'] | [0].name" -o tsv)
    if [[ "$frontend_easy_auth_secret" != "$easy_auth_secret_name" ]]; then
      fatal "Terraform-managed secret '${easy_auth_secret_name}' must exist on the frontend Container App before deployment."
    fi

    frontend_origin="${frontend_url%/}"
    if [[ -z "$frontend_origin" ]]; then
      frontend_fqdn=$(az containerapp show \
        --name "$frontend_app" \
        --resource-group "$rg" \
        --query properties.configuration.ingress.fqdn -o tsv)
      frontend_origin="https://${frontend_fqdn}"
    fi
    frontend_url="$frontend_origin"

    web_redirect="${frontend_origin}/.auth/login/aad/callback"
    web_redirect_found=false
    while IFS= read -r redirect_uri; do
      if [[ "${redirect_uri%/}" == "$web_redirect" ]]; then
        web_redirect_found=true
        break
      fi
    done < <(az ad app show --id "$entra_client_id" --query web.redirectUris -o tsv)
    if [[ "$web_redirect_found" != "true" ]]; then
      fatal "The Easy Auth Web redirect URI ${web_redirect} is missing. Apply Terraform before deployment."
    fi

    assignment_required=$(az ad sp show --id "$entra_client_id" --query appRoleAssignmentRequired -o tsv)
    [[ "$assignment_required" == "false" ]] || fatal "Assignment-required issuance needs an approved Application-role design."
    caller_identity_id=$(jq -r '.caller_identity_id' <<<"$caller_auth")
    jq -e --arg identity "$identity_id" --arg caller "$caller_identity_id" \
      '.identity.userAssignedIdentities | with_entries(.key |= ascii_downcase) | has($identity | ascii_downcase) and (has($caller | ascii_downcase) | not)' \
      <<<"$backend_state" >/dev/null || fatal "Backend identity attachment differs from the storage-only contract."
    jq -e --arg identity "$identity_id" --arg caller "$caller_identity_id" \
      '.identity.userAssignedIdentities | with_entries(.key |= ascii_downcase) | has($identity | ascii_downcase) and has($caller | ascii_downcase)' \
      <<<"$frontend_state" >/dev/null || fatal "Apply the frontend caller identity attachment before deployment."

    if verify_snapshot_ready "$backend_state" && verify_snapshot_ready "$frontend_state" &&
      verify_backend_auth_enforced && verify_frontend_runtime "$frontend_state" "$old_helper_image" &&
      verify_frontend_auth_enforced && verify_anonymous_request_denied; then
      old_rollout_secure=true
      old_backend_manifest=$(build_app_manifest "$backend_state" backend "$old_backend_image" managed_identity "$old_backend_image")
      old_frontend_pending_manifest=$(build_app_manifest "$frontend_state" frontend "$old_frontend_image" pending "$old_helper_image")
      old_frontend_manifest=$(build_app_manifest "$frontend_state" frontend "$old_frontend_image" managed_identity "$old_helper_image")
    fi

    if [[ ( "$skip_backend" == "true" || "$skip_frontend" == "true" ) && "$old_rollout_secure" != "true" ]]; then
      fatal "Partial hosted updates require an already verified managed-identity pair; use a full migration first."
    fi
    if [[ "$skip_backend" == "true" ]]; then
      [[ "$old_helper_image" == *@sha256:* ]] || fatal "Frontend-only updates require a verified digest-pinned helper image."
      helper_image="$old_helper_image"
    fi
  else
    if [[ "$skip_backend" == "true" ]]; then
      jq -e '[.properties.template.containers[] | select(.name == "backend") | .env[]? | select(.name == "DATAVIEWER_AUTH_DISABLED") | .value] == ["true"]' \
        <<<"$backend_state" >/dev/null || fatal "Skipped backend is incompatible with non-hosted mode."
    fi
    if [[ "$skip_frontend" == "true" ]]; then
      jq -e '[.properties.template.containers[] | select(.name == "frontend") | .env[]? | select(.name == "DATAVIEWER_CALLER_MODE") | .value] == ["off"]' \
        <<<"$frontend_state" >/dev/null || fatal "Skipped frontend is incompatible with non-hosted mode."
    fi
  fi
fi

#------------------------------------------------------------------------------
# Build Container Images
#------------------------------------------------------------------------------

if [[ "$skip_build" == "false" ]]; then

  if [[ "$local_build" == "true" ]]; then
    info "Signing Docker in to $acr_login_server..."
    az acr login --name "$acr_name"
  fi

  if [[ "$skip_backend" == "false" ]]; then
    section "Building Backend Image"
    backend_context="$(mktemp -d)"
    trap 'rm -rf -- "$backend_context"' EXIT
    mkdir -p "$backend_context/data-management/viewer/backend" "$backend_context/evaluation/vlm_judge" \
      "$backend_context/data-management/curation/physical_ai_curation/adapters" \
      "$backend_context/data-management/dataset-io/physical_ai_dataset_io"
    tar -C "$SRC_DIR/backend" --exclude='__pycache__' --exclude='*.pyc' --exclude='.env*' \
      -cf - pyproject.toml uv.lock logging.json src \
      | tar -C "$backend_context/data-management/viewer/backend" -xf -
    cp "$backend_dockerfile" "$backend_context/data-management/viewer/backend/Dockerfile"
    cp "$REPO_ROOT/evaluation/vlm_judge/"*.py \
      "$REPO_ROOT/evaluation/vlm_judge/pyproject.toml" "$REPO_ROOT/evaluation/vlm_judge/uv.lock" \
      "$backend_context/evaluation/vlm_judge/"
    cp "$REPO_ROOT/data-management/curation/pyproject.toml" "$backend_context/data-management/curation/"
    cp "$REPO_ROOT/data-management/curation/physical_ai_curation/"*.py \
      "$backend_context/data-management/curation/physical_ai_curation/"
    cp "$REPO_ROOT/data-management/curation/physical_ai_curation/adapters/"*.py \
      "$backend_context/data-management/curation/physical_ai_curation/adapters/"
    cp "$REPO_ROOT/data-management/dataset-io/pyproject.toml" "$backend_context/data-management/dataset-io/"
    cp "$REPO_ROOT/data-management/dataset-io/physical_ai_dataset_io/"*.py \
      "$backend_context/data-management/dataset-io/physical_ai_dataset_io/"
    info "Building $backend_image ($build_mode)..."
    if [[ "$local_build" == "true" ]]; then
      docker buildx build \
        --platform "$DATAVIEWER_BUILD_PLATFORM" \
        --build-arg "BACKEND_EXTRAS=${backend_extras}" \
        --build-arg "UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX:-https://pypi.org/simple}" \
        --build-arg "JUDGE_EXTRAS=${judge_extras}" \
        --file "$backend_context/data-management/viewer/backend/Dockerfile" \
        --tag "$backend_image" \
        --push \
        "$backend_context"
    else
      az acr build \
        --registry "$acr_name" \
        --image "${DATAVIEWER_BACKEND_IMAGE}:${image_tag}" \
        --build-arg "BACKEND_EXTRAS=${backend_extras}" \
        --build-arg "UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX:-https://pypi.org/simple}" \
        --build-arg "JUDGE_EXTRAS=${judge_extras}" \
        --file "data-management/viewer/backend/Dockerfile" \
        "$backend_context"
    fi
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    section "Building Frontend Image"
    info "Building $frontend_image..."

    if [[ "$local_build" == "true" ]]; then
      docker buildx build \
        --platform "$DATAVIEWER_BUILD_PLATFORM" \
        --file "$frontend_dockerfile" \
        --tag "$frontend_image" \
        --push \
        "$REPO_ROOT"
    else
      az acr build \
        --registry "$acr_name" \
        --image "${DATAVIEWER_FRONTEND_IMAGE}:${image_tag}" \
        --file "data-management/viewer/frontend/Dockerfile" \
        "$REPO_ROOT"
    fi
  fi
fi

#------------------------------------------------------------------------------
# Configure Authentication and Update Container Apps
#------------------------------------------------------------------------------

if [[ "$skip_update" == "false" ]]; then
  runtime_mode=off
  [[ "$auth_enabled" == "true" ]] && runtime_mode=managed_identity
  if [[ "$skip_backend" == "false" ]]; then
    backend_image=$(pin_registry_image "$backend_image") || fatal "Selected backend image is unavailable from the configured registry."
    helper_image="$backend_image"
    backend_manifest=$(build_app_manifest "$backend_state" backend "$backend_image" "$runtime_mode" "$helper_image")
  fi
  if [[ "$skip_frontend" == "false" ]]; then
    frontend_image=$(pin_registry_image "$frontend_image") || fatal "Selected frontend image is unavailable from the configured registry."
    if [[ "$auth_enabled" == "true" ]]; then
      helper_image=$(pin_registry_image "$helper_image") || fatal "Compatible caller helper image is unavailable."
      frontend_pending_manifest=$(build_app_manifest "$frontend_state" frontend "$frontend_image" pending "$helper_image")
    fi
    frontend_manifest=$(build_app_manifest "$frontend_state" frontend "$frontend_image" "$runtime_mode" "$helper_image")
  fi
  if [[ "$skip_backend" == "false" ]]; then
    az containerapp registry set --name "$backend_app" --resource-group "$rg" \
      --server "$acr_login_server" --identity "$identity_id" --output none
  fi
  if [[ "$skip_frontend" == "false" ]]; then
    az containerapp registry set --name "$frontend_app" --resource-group "$rg" \
      --server "$acr_login_server" --identity "$identity_id" --output none
  fi
fi

if [[ "$skip_update" == "false" && "$auth_enabled" == "true" ]]; then
  if ! deploy_authenticated_pair; then
    recover_authenticated_rollout || rollback_failed
    fatal "Managed-identity rollout failed; a verified MI pair was restored or frontend ingress was closed."
  fi

elif [[ "$skip_update" == "false" ]]; then
  if [[ "$skip_backend" == "false" ]]; then
    apply_app_manifest "$backend_app" "$backend_manifest" >/dev/null
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    apply_app_manifest "$frontend_app" "$frontend_manifest" >/dev/null
    info "Allowing anonymous access on $frontend_app..."
    az containerapp auth update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --enabled false \
      --output none
  fi
fi

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Backend Image" "$backend_image"
print_kv "Frontend Image" "$frontend_image"
print_kv "Backend App" "$backend_app"
print_kv "Frontend App" "$frontend_app"
print_kv "Image Tag" "$image_tag"
print_kv "Build" "$([[ "$skip_build" == "true" ]] && echo 'Skipped' || echo "Complete ($build_mode)")"
print_kv "Update" "$([[ "$skip_update" == "true" ]] && echo 'Skipped' || echo 'Complete')"
print_kv "Easy Auth" "$easy_auth_status"
print_kv "Backend Storage" "$backend_storage_status"
[[ -n "$frontend_url" ]] && print_kv "Frontend URL" "$frontend_url"
info "Deployment complete"
