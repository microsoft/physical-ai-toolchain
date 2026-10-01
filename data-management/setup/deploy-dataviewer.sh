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
    --skip-backend           Skip backend build/deploy
    --skip-frontend          Skip frontend build/deploy
    --config-preview         Print configuration and exit

When building images, the tag defaults to 'sha-<git-short-hash>' for unique
revisions. Use --tag to override, or --skip-build to reference existing images.

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --tag v0.1.0
    $(basename "$0") --skip-build
    $(basename "$0") --skip-frontend --tag sha-abc1234
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
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform jq

wait_for_ready_revision() {
  local app="$1"
  local rg="$2"
  local expected_revision ready_revision

  expected_revision=$(az containerapp show \
    --name "$app" \
    --resource-group "$rg" \
    --query properties.latestRevisionName -o tsv)

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

operator_auth_gate() {
  local expected_mode="$1"
  local stage="$2"
  local confirmation

  info "Authentication gate: $stage"
  info "Open $frontend_url in a private browser window and sign in."
  info "In browser developer tools, confirm /api/auth/context sends an Authorization header."
  info "Confirm the response is HTTP 200 with auth_mode=$expected_mode."
  if ! IFS= read -r -t 900 -p "Type 'yes' within 15 minutes to confirm this gate, or anything else to roll back: " confirmation; then
    error "Authentication gate timed out or input ended before confirmation"
    return 1
  fi
  [[ "$confirmation" == "yes" ]]
}

set_backend_provider() {
  local provider="$1"

  az containerapp update \
    --name "$backend_app" \
    --resource-group "$rg" \
    --set-env-vars \
      "DATAVIEWER_AUTH_PROVIDER=${provider}" \
      "DATAVIEWER_AUTH_DISABLED=false" \
      "DATAVIEWER_AZURE_TENANT_ID=${entra_tenant_id}" \
      "DATAVIEWER_AZURE_CLIENT_ID=${entra_client_id}" \
    --output none
  wait_for_ready_revision "$backend_app" "$rg"
}

allow_frontend_client_application() {
  local auth_config auth_config_id allowed_applications updated_auth_config

  auth_config_id=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query id -o tsv)
  auth_config_id="${auth_config_id}/authConfigs/current"
  auth_config=$(az rest \
    --method get \
    --url "https://management.azure.com${auth_config_id}?api-version=2024-03-01")
  allowed_applications=$(jq -c \
    '.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedApplications // []' \
    <<< "$auth_config")

  if jq -e --arg client_id "$entra_client_id" \
    'index($client_id) != null' <<< "$allowed_applications" >/dev/null; then
    return 0
  fi

  updated_auth_config=$(jq -c \
    --arg client_id "$entra_client_id" \
    '.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedApplications += [$client_id]
      | .properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedApplications |= unique
      | {properties: .properties}' \
    <<< "$auth_config")
  az rest \
    --method put \
    --url "https://management.azure.com${auth_config_id}?api-version=2024-03-01" \
    --headers Content-Type=application/json \
    --body "$updated_auth_config" \
    --output none

  auth_config=$(az rest \
    --method get \
    --url "https://management.azure.com${auth_config_id}?api-version=2024-03-01")
  jq -e --arg client_id "$entra_client_id" \
    '.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedApplications | index($client_id) != null' \
    <<< "$auth_config" >/dev/null
}

restore_backend_auth() {
  local -a update_args restore_env remove_env

  restore_env=("DATAVIEWER_AUTH_DISABLED=${old_auth_disabled}")
  remove_env=()
  if [[ -n "$old_provider" ]]; then
    restore_env+=("DATAVIEWER_AUTH_PROVIDER=${old_provider}")
  else
    remove_env+=(DATAVIEWER_AUTH_PROVIDER)
  fi
  if [[ -n "$old_tenant_id" ]]; then
    restore_env+=("DATAVIEWER_AZURE_TENANT_ID=${old_tenant_id}")
  else
    remove_env+=(DATAVIEWER_AZURE_TENANT_ID)
  fi
  if [[ -n "$old_client_id" ]]; then
    restore_env+=("DATAVIEWER_AZURE_CLIENT_ID=${old_client_id}")
  else
    remove_env+=(DATAVIEWER_AZURE_CLIENT_ID)
  fi

  update_args=(
    --name "$backend_app" \
    --resource-group "$rg" \
    --set-env-vars "${restore_env[@]}"
  )
  if [[ "${#remove_env[@]}" -gt 0 ]]; then
    update_args+=(--remove-env-vars "${remove_env[@]}")
  fi
  update_args+=(--output none)

  az containerapp update "${update_args[@]}"
  wait_for_ready_revision "$backend_app" "$rg"
}

restore_image() {
  local app="$1"
  local image="$2"

  az containerapp update \
    --name "$app" \
    --resource-group "$rg" \
    --image "$image" \
    --output none
  wait_for_ready_revision "$app" "$rg"
}

restore_frontend_auth() {
  local restore_failed=false

  if [[ "$frontend_auth_changed" != "true" ]]; then
    return 0
  fi

  info "Restoring the previous disabled frontend Easy Auth state..."
  az containerapp auth update \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --enabled false \
    --output none || restore_failed=true

  if [[ -n "$new_credential_key_id" ]]; then
    az ad app credential delete \
      --id "$entra_client_id" \
      --key-id "$new_credential_key_id" \
      --output none || restore_failed=true
  fi

  [[ "$restore_failed" == "false" ]]
}

restore_frontend_rollout() {
  local restore_failed=false

  restore_image "$frontend_app" "$old_frontend_image" || restore_failed=true
  restore_frontend_auth || restore_failed=true

  [[ "$restore_failed" == "false" ]]
}

restore_provider_and_frontend() {
  local restore_failed=false

  restore_backend_auth || restore_failed=true
  restore_frontend_rollout || restore_failed=true

  [[ "$restore_failed" == "false" ]]
}

restore_complete_rollout() {
  local restore_failed=false

  restore_image "$backend_app" "$old_backend_image" || restore_failed=true
  restore_backend_auth || restore_failed=true
  restore_frontend_rollout || restore_failed=true

  [[ "$restore_failed" == "false" ]]
}

rollback_failed() {
  local recovery_command="$1"

  error "Automatic rollback failed. Run this recovery command:"
  error "$recovery_command"
  exit 1
}

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

if [[ "$config_preview" == "true" ]]; then
  info "Config preview mode — exiting without changes."
  exit 0
fi

#------------------------------------------------------------------------------
# Capture Rollback State and Validate Partial Deployment
#------------------------------------------------------------------------------

old_backend_image=""
old_frontend_image=""
old_provider=""
old_auth_disabled="false"
old_tenant_id=""
old_client_id=""
provider_recovery_command=""
provider_recovery_remove_vars=""
frontend_auth_changed=false
new_credential_key_id=""
frontend_auth_recovery_command="true"
if [[ "$skip_update" == "false" ]]; then
  old_backend_image=$(az containerapp show \
    --name "$backend_app" \
    --resource-group "$rg" \
    --query 'properties.template.containers[0].image' -o tsv)
  old_frontend_image=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query 'properties.template.containers[0].image' -o tsv)
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
  provider_recovery_command="az containerapp update --name '$backend_app' --resource-group '$rg' --set-env-vars DATAVIEWER_AUTH_DISABLED='$old_auth_disabled'"
  if [[ -n "$old_provider" ]]; then
    provider_recovery_command+=" DATAVIEWER_AUTH_PROVIDER='$old_provider'"
  else
    provider_recovery_remove_vars+=" DATAVIEWER_AUTH_PROVIDER"
  fi
  if [[ -n "$old_tenant_id" ]]; then
    provider_recovery_command+=" DATAVIEWER_AZURE_TENANT_ID='$old_tenant_id'"
  else
    provider_recovery_remove_vars+=" DATAVIEWER_AZURE_TENANT_ID"
  fi
  if [[ -n "$old_client_id" ]]; then
    provider_recovery_command+=" DATAVIEWER_AZURE_CLIENT_ID='$old_client_id'"
  else
    provider_recovery_remove_vars+=" DATAVIEWER_AZURE_CLIENT_ID"
  fi
  if [[ -n "$provider_recovery_remove_vars" ]]; then
    provider_recovery_command+=" --remove-env-vars${provider_recovery_remove_vars}"
  fi

  if [[ "$auth_enabled" == "true" && "$skip_frontend" == "true" && "$old_provider" != "azure_ad" ]]; then
    fatal "--skip-frontend requires an existing, browser-verified azure_ad backend provider; current provider is ${old_provider:-unset}"
  fi
  if [[ "$auth_enabled" == "true" && "$skip_backend" == "true" && "$old_provider" != "easy_auth" && "$old_provider" != "azure_ad" ]]; then
    fatal "--skip-backend requires an existing easy_auth or azure_ad backend provider; current provider is ${old_provider:-unset}"
  fi
  if [[ "$auth_enabled" == "true" && "$old_provider" != "" && "$old_provider" != "apikey" && "$old_provider" != "easy_auth" && "$old_provider" != "azure_ad" ]]; then
    fatal "Authenticated rollout cannot migrate unsupported provider ${old_provider}"
  fi
  if [[ "$auth_enabled" == "true" && ( ! -t 0 || ! -t 1 ) ]]; then
    fatal "Authenticated rollout requires an interactive terminal for browser validation gates."
  fi
  if [[ "$auth_enabled" == "true" ]]; then
    frontend_origin="${frontend_url%/}"
    if [[ -z "$frontend_origin" ]]; then
      frontend_fqdn=$(az containerapp show \
        --name "$frontend_app" \
        --resource-group "$rg" \
        --query properties.configuration.ingress.fqdn -o tsv)
      frontend_origin="https://${frontend_fqdn}"
    fi
    frontend_url="$frontend_origin"

    spa_redirect_found=false
    while IFS= read -r redirect_uri; do
      if [[ "${redirect_uri%/}" == "$frontend_origin" ]]; then
        spa_redirect_found=true
        break
      fi
    done < <(az ad app show --id "$entra_client_id" --query spa.redirectUris -o tsv)
    if [[ "$spa_redirect_found" != "true" ]]; then
      fatal "The production SPA redirect URI ${frontend_origin}/ is missing. Register it on the Entra application before deployment."
    fi

    web_redirect="${frontend_origin}/.auth/login/aad/callback"
    web_redirect_found=false
    while IFS= read -r redirect_uri; do
      if [[ "${redirect_uri%/}" == "$web_redirect" ]]; then
        web_redirect_found=true
        break
      fi
    done < <(az ad app show --id "$entra_client_id" --query web.redirectUris -o tsv)
    if [[ "$web_redirect_found" != "true" ]]; then
      fatal "The Easy Auth Web redirect URI ${web_redirect} is missing. Register it on the Entra application before deployment."
    fi
  fi
  if [[ "$auth_enabled" == "true" && "$skip_frontend" == "true" ]]; then
    operator_auth_gate "azure_ad" "preflight for backend-only deployment" ||
      fatal "Backend-only deployment requires a successful azure_ad browser gate before mutation."
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

SRC_DIR="$SCRIPT_DIR/../viewer"

if [[ "$skip_build" == "false" ]]; then

  if [[ "$skip_backend" == "false" ]]; then
    section "Building Backend Image"
    info "Building $backend_image..."
    az acr build \
      --registry "$acr_name" \
      --image "${DATAVIEWER_BACKEND_IMAGE}:${image_tag}" \
      --file "$SRC_DIR/backend/Dockerfile" \
      "$SRC_DIR/backend/"
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    section "Building Frontend Image"
    info "Building $frontend_image..."

    build_args=()
    if [[ "$auth_enabled" == "true" ]]; then
      build_args+=(--build-arg "VITE_AZURE_CLIENT_ID=${entra_client_id}")
      build_args+=(--build-arg "VITE_AZURE_TENANT_ID=${entra_tenant_id}")
      info "Entra ID auth enabled — injecting MSAL build args"
    fi

    az acr build \
      --registry "$acr_name" \
      --image "${DATAVIEWER_FRONTEND_IMAGE}:${image_tag}" \
      ${build_args[@]+"${build_args[@]}"} \
      --file "data-management/viewer/frontend/Dockerfile" \
      "$REPO_ROOT"
  fi
fi

#------------------------------------------------------------------------------
# Configure Frontend Easy Auth
#------------------------------------------------------------------------------

if [[ "$auth_enabled" == "true" && "$skip_update" == "false" && "$skip_frontend" == "false" ]]; then
  section "Configuring Easy Auth on Frontend"

  easy_auth_enabled=$(az containerapp auth show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query platform.enabled -o tsv)

  if [[ "$easy_auth_enabled" != "true" ]]; then
    credential_display_name="easy-auth-${image_tag}-$(date -u +%Y%m%d%H%M%S)"
    info "Appending a client secret for the Easy Auth server-directed flow..."
    entra_app_object_id=$(az ad app show --id "$entra_client_id" --query id -o tsv)
    credential_response=$(az rest \
      --method post \
      --uri "https://graph.microsoft.com/v1.0/applications/${entra_app_object_id}/addPassword" \
      --headers Content-Type=application/json \
      --body "$(jq -cn --arg name "$credential_display_name" \
        '{passwordCredential: {displayName: $name}}')")
    client_secret=$(jq -r '.secretText // empty' <<< "$credential_response")
    new_credential_key_id=$(jq -r '.keyId // empty' <<< "$credential_response")
    if [[ -z "$client_secret" || -z "$new_credential_key_id" ]]; then
      if [[ -n "$new_credential_key_id" ]]; then
        az ad app credential delete \
          --id "$entra_client_id" \
          --key-id "$new_credential_key_id" \
          --output none || true
      fi
      fatal "Microsoft Graph did not return the appended Easy Auth credential."
    fi
    frontend_auth_changed=true
    frontend_auth_recovery_command="az containerapp auth update --name '$frontend_app' --resource-group '$rg' --enabled false && az ad app credential delete --id '$entra_client_id' --key-id '$new_credential_key_id'"

    info "Configuring the Easy Auth Microsoft provider..."
    if ! az containerapp auth microsoft update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --client-id "$entra_client_id" \
      --client-secret "$client_secret" \
      --issuer "https://login.microsoftonline.com/${entra_tenant_id}/v2.0" \
      --yes \
      --output none ||
      ! az containerapp auth update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --enabled true \
      --unauthenticated-client-action RedirectToLoginPage \
      --redirect-provider azureactivedirectory \
      --output none; then
      restore_frontend_auth ||
        rollback_failed "$frontend_auth_recovery_command"
      fatal "Frontend Easy Auth configuration failed and was rolled back."
    fi
    easy_auth_enabled=$(az containerapp auth show \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --query platform.enabled -o tsv)
    if [[ "$easy_auth_enabled" != "true" ]]; then
      restore_frontend_auth ||
        rollback_failed "$frontend_auth_recovery_command"
      fatal "Frontend Easy Auth remained disabled after configuration and was rolled back."
    fi
  else
    info "Easy Auth is already enabled; preserving its existing credential."
  fi
  if ! allow_frontend_client_application; then
    restore_frontend_auth ||
      rollback_failed "$frontend_auth_recovery_command"
    fatal "The frontend client application could not be allowed through Easy Auth."
  fi
fi

#------------------------------------------------------------------------------
# Update Container Apps
#------------------------------------------------------------------------------

if [[ "$skip_update" == "false" && "$auth_enabled" == "true" ]]; then
  if [[ "$skip_frontend" == "false" ]]; then
    section "Updating Frontend Container App"
    info "Deploying token-capable frontend $frontend_image..."
    if ! az containerapp update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --image "$frontend_image" \
      --output none || ! wait_for_ready_revision "$frontend_app" "$rg"; then
      restore_frontend_rollout ||
        rollback_failed "az containerapp update --name '$frontend_app' --resource-group '$rg' --image '$old_frontend_image' && $frontend_auth_recovery_command"
      fatal "Frontend deployment failed and was rolled back."
    fi

    if [[ "$old_provider" == "easy_auth" || "$old_provider" == "azure_ad" ]]; then
      if ! operator_auth_gate "$old_provider" "frontend deployed; backend provider unchanged"; then
        restore_frontend_rollout ||
          rollback_failed "az containerapp update --name '$frontend_app' --resource-group '$rg' --image '$old_frontend_image' && $frontend_auth_recovery_command"
        fatal "Frontend authentication gate failed and the frontend was rolled back."
      fi
    fi
  fi

  if [[ "$skip_backend" == "true" ]]; then
    info "Backend update skipped; provider remains $old_provider."
  else
    if [[ "$old_provider" != "azure_ad" ]]; then
      section "Switching Backend Authentication Provider"
      info "Changing the existing backend provider to azure_ad before deploying the new backend image..."
      if ! set_backend_provider "azure_ad"; then
        if ! restore_provider_and_frontend; then
          rollback_failed "$provider_recovery_command && az containerapp update --name '$frontend_app' --resource-group '$rg' --image '$old_frontend_image' && $frontend_auth_recovery_command"
        fi
        fatal "Backend provider transition failed and was rolled back."
      fi

      if ! operator_auth_gate "azure_ad" "existing backend switched to scoped JWT validation"; then
        if ! restore_provider_and_frontend; then
          rollback_failed "$provider_recovery_command && az containerapp update --name '$frontend_app' --resource-group '$rg' --image '$old_frontend_image' && $frontend_auth_recovery_command"
        fi
        fatal "Provider authentication gate failed and the provider and frontend were rolled back."
      fi
    fi

    section "Updating Backend Container App"
    info "Deploying $backend_image with azure_ad already active..."
    if ! az containerapp update \
      --name "$backend_app" \
      --resource-group "$rg" \
      --image "$backend_image" \
      --output none || ! wait_for_ready_revision "$backend_app" "$rg"; then
      if ! restore_complete_rollout; then
        rollback_failed "az containerapp update --name '$backend_app' --resource-group '$rg' --image '$old_backend_image' && $provider_recovery_command && az containerapp update --name '$frontend_app' --resource-group '$rg' --image '$old_frontend_image' && $frontend_auth_recovery_command"
      fi
      fatal "Backend deployment failed and the backend image was rolled back."
    fi

    if ! operator_auth_gate "azure_ad" "new backend deployed"; then
      if ! restore_complete_rollout; then
        rollback_failed "az containerapp update --name '$backend_app' --resource-group '$rg' --image '$old_backend_image' && $provider_recovery_command && az containerapp update --name '$frontend_app' --resource-group '$rg' --image '$old_frontend_image' && $frontend_auth_recovery_command"
      fi
      fatal "Backend authentication gate failed and the backend image was rolled back."
    fi
  fi

elif [[ "$auth_enabled" == "false" && "$skip_update" == "false" ]]; then
  if [[ "$skip_backend" == "false" ]]; then
    section "Updating Backend Container App"
    az containerapp update \
      --name "$backend_app" \
      --resource-group "$rg" \
      --image "$backend_image" \
      --output none
    wait_for_ready_revision "$backend_app" "$rg"
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    section "Updating Frontend Container App"
    az containerapp update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --image "$frontend_image" \
      --output none
    wait_for_ready_revision "$frontend_app" "$rg"
  fi

  section "Disabling Authentication"

  if [[ "$skip_backend" == "false" ]]; then
    info "Setting auth-disabled env vars on $backend_app..."
    az containerapp update \
      --name "$backend_app" \
      --resource-group "$rg" \
      --set-env-vars "DATAVIEWER_AUTH_DISABLED=true" \
      --remove-env-vars \
        DATAVIEWER_AUTH_PROVIDER \
        DATAVIEWER_AZURE_TENANT_ID \
        DATAVIEWER_AZURE_CLIENT_ID \
      --output none
  fi

  if [[ "$skip_frontend" == "false" ]]; then
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
print_kv "Build" "$([[ "$skip_build" == "true" ]] && echo 'Skipped' || echo 'Complete')"
print_kv "Update" "$([[ "$skip_update" == "true" ]] && echo 'Skipped' || echo 'Complete')"
print_kv "Easy Auth" "$([[ "$auth_enabled" == "true" ]] && echo 'Configured' || echo 'Disabled')"
[[ -n "$frontend_url" ]] && print_kv "Frontend URL" "$frontend_url"
info "Deployment complete"
