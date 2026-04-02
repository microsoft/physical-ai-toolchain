#!/usr/bin/env bash
# Build and deploy the dataviewer application to Azure Container Apps
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../shared/lib/common.sh
source "$REPO_ROOT/shared/lib/common.sh"
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
      --file "$SRC_DIR/frontend/Dockerfile" \
      "$SRC_DIR/frontend/"
  fi
fi

#------------------------------------------------------------------------------
# Update Container Apps
#------------------------------------------------------------------------------

if [[ "$skip_update" == "false" ]]; then

  if [[ "$skip_backend" == "false" ]]; then
    section "Updating Backend Container App"
    info "Deploying $backend_image to $backend_app..."
    az containerapp update \
      --name "$backend_app" \
      --resource-group "$rg" \
      --image "$backend_image"
  fi

  if [[ "$skip_frontend" == "false" ]]; then
    section "Updating Frontend Container App"
    info "Deploying $frontend_image to $frontend_app..."
    az containerapp update \
      --name "$frontend_app" \
      --resource-group "$rg" \
      --image "$frontend_image"
  fi
fi

#------------------------------------------------------------------------------
# Configure Authentication
#------------------------------------------------------------------------------

if [[ "$auth_enabled" == "true" && "$skip_update" == "false" ]]; then

  section "Configuring Backend Authentication"
  info "Setting auth env vars on $backend_app..."
  az containerapp update \
    --name "$backend_app" \
    --resource-group "$rg" \
    --set-env-vars \
      "DATAVIEWER_AUTH_PROVIDER=easy_auth" \
      "DATAVIEWER_AUTH_DISABLED=false" \
      "DATAVIEWER_AZURE_TENANT_ID=${entra_tenant_id}" \
      "DATAVIEWER_AZURE_CLIENT_ID=${entra_client_id}" \
    --output none

  section "Configuring Easy Auth on Frontend"

  # Create client secret for server-directed OAuth flow
  info "Creating client secret for Easy Auth..."
  client_secret=$(az ad app credential reset \
    --id "$entra_client_id" \
    --display-name "easy-auth" \
    --years 2 \
    --query password -o tsv)

  # Enable ID token issuance (required for Easy Auth)
  info "Enabling ID token issuance..."
  az ad app update --id "$entra_client_id" \
    --enable-id-token-issuance true \
    --output none

  # Add web redirect URI for Easy Auth callback
  frontend_fqdn=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query 'properties.configuration.ingress.fqdn' -o tsv)

  info "Adding Easy Auth callback redirect URI..."
  az ad app update --id "$entra_client_id" \
    --web-redirect-uris "https://${frontend_fqdn}/.auth/login/aad/callback" \
    --output none

  # Configure Easy Auth identity provider
  info "Configuring Easy Auth Microsoft provider..."
  az containerapp auth microsoft update \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --client-id "$entra_client_id" \
    --client-secret "$client_secret" \
    --issuer "https://login.microsoftonline.com/${entra_tenant_id}/v2.0" \
    --yes \
    --output none

  # Require authentication for all requests
  info "Setting unauthenticated client action to RedirectToLoginPage..."
  az containerapp auth update \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --unauthenticated-client-action RedirectToLoginPage \
    --redirect-provider azureactivedirectory \
    --output none

elif [[ "$auth_enabled" == "false" && "$skip_update" == "false" ]]; then

  section "Disabling Authentication"

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

  info "Allowing anonymous access on $frontend_app..."
  az containerapp auth update \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --enabled false \
    --output none

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
