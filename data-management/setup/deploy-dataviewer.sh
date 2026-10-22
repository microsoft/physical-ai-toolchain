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

proxy_secret_name="dataviewer-proxy-key"
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

verify_app_rollout() {
  local app="$1"
  local revision="$2"
  local image="$3"
  local proxy_secret_ref="$4"
  local app_state

  app_state=$(az containerapp show \
    --name "$app" \
    --resource-group "$rg" \
    --output json) || return 1
  jq -e \
    --arg revision "$revision" \
    --arg image "$image" \
    --arg proxy_secret_ref "$proxy_secret_ref" \
    '
      .properties.latestRevisionName == $revision
      and .properties.latestReadyRevisionName == $revision
      and .properties.template.containers[0].image == $image
      and (
        ([.properties.template.containers[0].env[]? | select(.name == "DATAVIEWER_PROXY_KEY")][0].secretRef // "")
        == $proxy_secret_ref
      )
    ' <<<"$app_state" >/dev/null
}

verify_backend_auth_enforced() {
  local app_state

  app_state=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --output json) || return 1
  jq -e \
    --arg proxy_secret_name "$proxy_secret_name" \
    '
      ([.properties.template.containers[0].env[]? | select(.name == "DATAVIEWER_AUTH_PROVIDER")][0].value // "")
        == "easy_auth"
      and ([.properties.template.containers[0].env[]? | select(.name == "DATAVIEWER_AUTH_DISABLED")][0].value // "")
        == "false"
      and ([.properties.template.containers[0].env[]? | select(.name == "DATAVIEWER_PROXY_KEY")][0].secretRef // "")
        == $proxy_secret_name
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

  info "Authentication gate: proxy-bound Easy Auth rollout"
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
  local app="$1"
  local image="$2"
  local proxy_secret_ref="$3"
  shift 3
  local -a update_args env_args=("$@")

  update_args=(
    --image "$image"
  )
  if [[ -n "$proxy_secret_ref" ]]; then
    env_args+=("DATAVIEWER_PROXY_KEY=secretref:${proxy_secret_ref}")
  else
    update_args+=(--remove-env-vars DATAVIEWER_PROXY_KEY)
  fi
  if [[ "${#env_args[@]}" -gt 0 ]]; then
    update_args+=(--set-env-vars "${env_args[@]}")
  fi
  local revision
  revision=$(update_app_and_wait "$app" "${update_args[@]}") || return 1
  verify_app_rollout "$app" "$revision" "$image" "$proxy_secret_ref"
}

rollback_backend() {
  local -a update_args backend_env remove_env
  local revision

  backend_env=("DATAVIEWER_AUTH_DISABLED=${old_auth_disabled}")
  remove_env=()

  if [[ -n "$old_provider" ]]; then
    backend_env+=("DATAVIEWER_AUTH_PROVIDER=${old_provider}")
  else
    remove_env+=(DATAVIEWER_AUTH_PROVIDER)
  fi
  if [[ -n "$old_tenant_id" ]]; then
    backend_env+=("DATAVIEWER_AZURE_TENANT_ID=${old_tenant_id}")
  else
    remove_env+=(DATAVIEWER_AZURE_TENANT_ID)
  fi
  if [[ -n "$old_client_id" ]]; then
    backend_env+=("DATAVIEWER_AZURE_CLIENT_ID=${old_client_id}")
  else
    remove_env+=(DATAVIEWER_AZURE_CLIENT_ID)
  fi
  if [[ -n "$old_backend_proxy_secret_ref" ]]; then
    backend_env+=("DATAVIEWER_PROXY_KEY=secretref:${old_backend_proxy_secret_ref}")
  else
    remove_env+=(DATAVIEWER_PROXY_KEY)
  fi

  update_args=(
    --image "$old_backend_image"
    --set-env-vars "${backend_env[@]}"
  )
  if [[ "${#remove_env[@]}" -gt 0 ]]; then
    update_args+=(--remove-env-vars "${remove_env[@]}")
  fi
  revision=$(update_app_and_wait "$backend_app" "${update_args[@]}") || return 1
  verify_app_rollout \
    "$backend_app" \
    "$revision" \
    "$old_backend_image" \
    "$old_backend_proxy_secret_ref" || return 1
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
      "$old_frontend_image" \
      "$old_frontend_proxy_secret_ref" || recovery_failed=true
    verify_frontend_auth_enforced || recovery_failed=true
    verify_anonymous_request_denied || recovery_failed=true
  fi

  if [[ "$recovery_failed" == "true" ]]; then
    close_frontend_ingress || return 1
  fi
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
frontend_url=$(tf_get "$tf_output" "dataviewer.value.frontend.url" "")

# Entra ID auth configuration (empty when should_deploy_auth=false)
entra_client_id=$(tf_get "$tf_output" "dataviewer.value.entra_id.client_id" "")
entra_tenant_id=$(tf_get "$tf_output" "dataviewer.value.entra_id.tenant_id" "")
auth_enabled=false
if [[ -n "$entra_client_id" && "$entra_client_id" != "null" ]]; then
  auth_enabled=true
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

if [[ "$skip_build" == "false" && "$local_build" == "false" && ${#buildkit_images[@]} -gt 0 ]]; then
  fatal "ACR quick builds can't build the ${buildkit_images[*]} Dockerfile (BuildKit syntax). Rerun with --local-build, or skip that image."
fi

#------------------------------------------------------------------------------
# Capture Rollback State and Validate Authentication Rollout
#------------------------------------------------------------------------------

old_backend_image=""
old_frontend_image=""
old_provider=""
old_auth_disabled="false"
old_tenant_id=""
old_client_id=""
old_backend_proxy_secret_ref=""
old_frontend_proxy_secret_ref=""
old_rollout_secure=false

if [[ "$skip_update" == "false" ]]; then
  old_backend_image=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query properties.template.containers[0].image -o tsv)
  old_frontend_image=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query properties.template.containers[0].image -o tsv)
  old_provider=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='DATAVIEWER_AUTH_PROVIDER'] | [0].value" -o tsv)
  old_auth_disabled=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='DATAVIEWER_AUTH_DISABLED'] | [0].value" -o tsv)
  old_auth_disabled="${old_auth_disabled:-false}"
  old_tenant_id=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='DATAVIEWER_AZURE_TENANT_ID'] | [0].value" -o tsv)
  old_client_id=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='DATAVIEWER_AZURE_CLIENT_ID'] | [0].value" -o tsv)
  old_backend_proxy_secret_ref=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='DATAVIEWER_PROXY_KEY'] | [0].secretRef" -o tsv)
  old_frontend_proxy_secret_ref=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query "properties.template.containers[0].env[?name=='DATAVIEWER_PROXY_KEY'] | [0].secretRef" -o tsv)

  if [[ "$auth_enabled" == "true" ]]; then
    if [[ ! -t 0 || ! -t 1 ]]; then
      fatal "Authenticated rollout requires an interactive terminal for the final browser validation gate."
    fi

    backend_secret=$(az containerapp secret list \
      --name "$backend_app" \
      --resource-group "$rg" \
      --query "[?name=='${proxy_secret_name}'] | [0].name" -o tsv)
    frontend_secret=$(az containerapp secret list \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --query "[?name=='${proxy_secret_name}'] | [0].name" -o tsv)
    frontend_easy_auth_secret=$(az containerapp secret list \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --query "[?name=='${easy_auth_secret_name}'] | [0].name" -o tsv)
    if [[ "$backend_secret" != "$proxy_secret_name" || "$frontend_secret" != "$proxy_secret_name" ]]; then
      fatal "Terraform-managed secret '${proxy_secret_name}' must exist on both Container Apps before deployment."
    fi
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

    easy_auth_enabled=$(az containerapp auth show \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --query platform.enabled -o tsv)

    if [[ "$easy_auth_enabled" == "true" &&
      "$old_provider" == "easy_auth" &&
      "$old_auth_disabled" == "false" &&
      "$old_backend_proxy_secret_ref" == "$proxy_secret_name" &&
      "$old_frontend_proxy_secret_ref" == "$proxy_secret_name" ]]; then
      old_rollout_secure=true
    fi

    if [[ "$skip_backend" == "true" && ( "$old_provider" != "easy_auth" || "$old_backend_proxy_secret_ref" != "$proxy_secret_name" ) ]]; then
      fatal "--skip-backend requires an existing easy_auth backend that references secret '${proxy_secret_name}'."
    fi
    if [[ "$skip_frontend" == "true" && ( "$easy_auth_enabled" != "true" || "$old_frontend_proxy_secret_ref" != "$proxy_secret_name" ) ]]; then
      fatal "--skip-frontend requires enabled Easy Auth and an existing frontend reference to secret '${proxy_secret_name}'."
    fi
  fi
fi

#------------------------------------------------------------------------------
# Configure ACR Registry
#------------------------------------------------------------------------------

if [[ "$skip_update" == "false" ]]; then
  section "Configuring ACR Registry"

  if [[ "$skip_backend" == "false" ]]; then
    info "Ensuring ACR registry on $backend_app..."
    az containerapp registry set \
      --name "$backend_app" \
      --resource-group "$rg" \
      --server "$acr_login_server" \
      --identity "$identity_id" \
      --output none
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    info "Ensuring ACR registry on $frontend_app..."
    az containerapp registry set \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --server "$acr_login_server" \
      --identity "$identity_id" \
      --output none
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

if [[ "$skip_update" == "false" && "$auth_enabled" == "true" ]]; then
  if [[ "$skip_backend" == "false" ]]; then
    backend_revision=""
    section "Updating Backend Container App"
    info "Deploying $backend_image with proxy-bound Easy Auth..."
    if ! backend_revision=$(update_app_and_wait "$backend_app" \
      --image "$backend_image" \
      --set-env-vars \
        "$backend_storage_env" \
        "DATAVIEWER_AUTH_PROVIDER=easy_auth" \
        "DATAVIEWER_AUTH_DISABLED=false" \
        "DATAVIEWER_PROXY_KEY=secretref:${proxy_secret_name}" \
      --remove-env-vars \
        "$legacy_backend_storage_env" \
        DATAVIEWER_AZURE_TENANT_ID \
        DATAVIEWER_AZURE_CLIENT_ID) ||
      ! verify_app_rollout "$backend_app" "$backend_revision" "$backend_image" "$proxy_secret_name" ||
      ! verify_backend_auth_enforced; then
      recover_authenticated_rollout || rollback_failed
      fatal "Backend deployment failed; a verified secure pair was restored or frontend ingress was closed."
    fi
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    section "Configuring Easy Auth on Frontend"
    if ! az containerapp auth microsoft update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --client-id "$entra_client_id" \
      --client-secret-name "$easy_auth_secret_name" \
      --issuer "https://login.microsoftonline.com/${entra_tenant_id}/v2.0" \
      --yes \
      --output none ||
      ! az containerapp auth update \
        --name "$frontend_app" \
        --resource-group "$rg" \
        --enabled true \
        --unauthenticated-client-action RedirectToLoginPage \
        --redirect-provider azureactivedirectory \
        --output none ||
      ! verify_frontend_auth_enforced ||
      ! ensure_frontend_ingress ||
      ! verify_anonymous_request_denied; then
      recover_authenticated_rollout || rollback_failed
      fatal "Frontend Easy Auth configuration failed; a verified secure pair was restored or frontend ingress was closed."
    fi

    frontend_revision=""
    section "Updating Frontend Container App"
    info "Deploying $frontend_image with the proxy credential reference..."
    if ! frontend_revision=$(update_app_and_wait "$frontend_app" \
      --image "$frontend_image" \
      --set-env-vars "DATAVIEWER_PROXY_KEY=secretref:${proxy_secret_name}") ||
      ! verify_app_rollout "$frontend_app" "$frontend_revision" "$frontend_image" "$proxy_secret_name" ||
      ! verify_frontend_auth_enforced ||
      ! verify_anonymous_request_denied; then
      recover_authenticated_rollout || rollback_failed
      fatal "Frontend deployment failed; a verified secure pair was restored or frontend ingress was closed."
    fi
  fi

  if ! operator_auth_gate; then
    recover_authenticated_rollout || rollback_failed
    fatal "Authentication validation failed; a verified secure pair was restored or frontend ingress was closed."
  fi

elif [[ "$skip_update" == "false" ]]; then
  if [[ "$skip_backend" == "false" ]]; then
    backend_revision=""
    section "Updating Backend Container App"
    backend_revision=$(update_app_and_wait "$backend_app" \
      --image "$backend_image" \
      --set-env-vars \
        "$backend_storage_env" \
        "DATAVIEWER_AUTH_DISABLED=true" \
      --remove-env-vars \
        "$legacy_backend_storage_env" \
        DATAVIEWER_AUTH_PROVIDER \
        DATAVIEWER_AZURE_TENANT_ID \
        DATAVIEWER_AZURE_CLIENT_ID \
        DATAVIEWER_PROXY_KEY)
    verify_app_rollout "$backend_app" "$backend_revision" "$backend_image" ""
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    frontend_revision=""
    section "Updating Frontend Container App"
    frontend_revision=$(update_app_and_wait "$frontend_app" \
      --image "$frontend_image" \
      --remove-env-vars DATAVIEWER_PROXY_KEY)
    verify_app_rollout "$frontend_app" "$frontend_revision" "$frontend_image" ""

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
