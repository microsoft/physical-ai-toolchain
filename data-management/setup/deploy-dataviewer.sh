#!/usr/bin/env bash
# Deploy signed dataviewer images to Azure Container Apps.
#
# This script does NOT build images. Images are built and signed by the
# container-publish.yml GitHub Actions workflow. Operators must supply the
# signed image digests, which are verified locally via
# scripts/security/verify-image.sh before any container app is updated.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

VERIFY_SCRIPT="$REPO_ROOT/scripts/security/verify-image.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy signed dataviewer images to Azure Container Apps. Image digests are
verified with cosign or notation before any update is applied.

OPTIONS:
    -h, --help                   Show this help message
    -t, --tf-dir DIR             Terraform directory (default: $DEFAULT_TF_DIR)
    --backend-digest DIGEST      Backend image digest (sha256:...) — required unless --skip-backend
    --frontend-digest DIGEST     Frontend image digest (sha256:...) — required unless --skip-frontend
    --verify-mode MODE           sigstore | notation | auto (default: auto)
    --offline                    Pass --offline to verify-image.sh (uses --trusted-root bundle)
    --trusted-root PATH          Sigstore trusted-root JSON for offline verification
    --policy-file PATH           Notation trust policy file
    --accept-public-rekor        Acknowledge that online Sigstore verification publishes
                                 signer identity + image digest to the public Rekor log
    --skip-backend               Skip backend deploy
    --skip-frontend              Skip frontend deploy
    --skip-update                Verify digests only; do not update container apps
    --config-preview             Print configuration and exit without contacting Azure

Image digests are produced by the container-publish.yml workflow. Use the
"sha256:..." value emitted by that workflow's signing step.

EXAMPLES:
    $(basename "$0") \\
        --backend-digest sha256:abc... --frontend-digest sha256:def...

    $(basename "$0") --offline --trusted-root ./trusted-root.json \\
        --backend-digest sha256:abc... --skip-frontend
EOF
}

# Defaults
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
backend_digest=""
frontend_digest=""
verify_mode="auto"
offline=false
trusted_root=""
policy_file=""
accept_public_rekor=false
skip_backend=false
skip_frontend=false
skip_update=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    -t|--tf-dir)           tf_dir="$2"; shift 2 ;;
    --backend-digest)      backend_digest="$2"; shift 2 ;;
    --frontend-digest)     frontend_digest="$2"; shift 2 ;;
    --verify-mode)         verify_mode="$2"; shift 2 ;;
    --offline)             offline=true; shift ;;
    --trusted-root)        trusted_root="$2"; shift 2 ;;
    --policy-file)         policy_file="$2"; shift 2 ;;
    --accept-public-rekor) accept_public_rekor=true; shift ;;
    --skip-backend)        skip_backend=true; shift ;;
    --skip-frontend)       skip_frontend=true; shift ;;
    --skip-update)         skip_update=true; shift ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

require_tools az terraform jq

# Validate digest format only when the corresponding component is in scope.
validate_digest() {
  local label="$1" value="$2"
  if [[ -z "$value" ]]; then
    fatal "$label digest is required (use --${label,,}-digest sha256:...)"
  fi
  if [[ ! "$value" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    fatal "$label digest must match sha256:<64 hex chars> (got: $value)"
  fi
}

if [[ "$skip_backend" == "false" ]]; then
  validate_digest "Backend" "$backend_digest"
fi
if [[ "$skip_frontend" == "false" ]]; then
  validate_digest "Frontend" "$frontend_digest"
fi

case "$verify_mode" in
  sigstore|notation|auto) ;;
  *) fatal "--verify-mode must be one of: sigstore, notation, auto (got: $verify_mode)" ;;
esac

# Config preview: print CLI-resolved configuration and exit before any
# terraform-state read or Azure call. Terraform outputs are intentionally
# omitted here because preview must work in isolated environments.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration (preview)"
  print_kv "Terraform Dir" "$tf_dir"
  print_kv "Backend Digest" "${skip_backend:+<skipped>}${skip_backend:-$backend_digest}"
  print_kv "Frontend Digest" "${skip_frontend:+<skipped>}${skip_frontend:-$frontend_digest}"
  print_kv "Verify Mode" "$verify_mode"
  print_kv "Offline" "$offline"
  print_kv "Trusted Root" "${trusted_root:-<none>}"
  print_kv "Policy File" "${policy_file:-<none>}"
  print_kv "Public Rekor Consent" "$accept_public_rekor"
  print_kv "Skip Backend" "$skip_backend"
  print_kv "Skip Frontend" "$skip_frontend"
  print_kv "Skip Update" "$skip_update"
  info "Config preview mode — exiting without contacting terraform or Azure."
  exit 0
fi

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
acr_name=$(tf_require "$tf_output" "container_registry.value.name" "ACR name")
acr_login_server=$(tf_require "$tf_output" "container_registry.value.login_server" "ACR login server")

dataviewer_deployed=$(tf_get "$tf_output" "dataviewer.value" "")
if [[ -z "$dataviewer_deployed" || "$dataviewer_deployed" == "null" ]]; then
  fatal "Dataviewer is not deployed. Set should_deploy_dataviewer=true in terraform.tfvars and run terraform apply first."
fi

backend_app=$(tf_require "$tf_output" "dataviewer.value.backend.name" "Backend container app name")
frontend_app=$(tf_require "$tf_output" "dataviewer.value.frontend.name" "Frontend container app name")
identity_id=$(tf_require "$tf_output" "dataviewer.value.identity.id" "Managed identity resource ID")
frontend_url=$(tf_get "$tf_output" "dataviewer.value.frontend.url" "")

entra_client_id=$(tf_get "$tf_output" "dataviewer.value.entra_id.client_id" "")
entra_tenant_id=$(tf_get "$tf_output" "dataviewer.value.entra_id.tenant_id" "")
auth_enabled=false
if [[ -n "$entra_client_id" && "$entra_client_id" != "null" ]]; then
  auth_enabled=true
fi

backend_repo="${acr_login_server}/${DATAVIEWER_BACKEND_IMAGE}"
frontend_repo="${acr_login_server}/${DATAVIEWER_FRONTEND_IMAGE}"
backend_image="${backend_repo}@${backend_digest}"
frontend_image="${frontend_repo}@${frontend_digest}"

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

section "Configuration"
print_kv "Resource Group" "$rg"
print_kv "ACR" "$acr_name"
print_kv "Backend Image" "${skip_backend:+<skipped>}${skip_backend:-$backend_image}"
print_kv "Frontend Image" "${skip_frontend:+<skipped>}${skip_frontend:-$frontend_image}"
print_kv "Backend App" "$backend_app"
print_kv "Frontend App" "$frontend_app"
print_kv "Identity" "${identity_id##*/}"
print_kv "Verify Mode" "$verify_mode"
print_kv "Offline" "$offline"
print_kv "Trusted Root" "${trusted_root:-<none>}"
print_kv "Policy File" "${policy_file:-<none>}"
print_kv "Public Rekor Consent" "$accept_public_rekor"
print_kv "Skip Update" "$skip_update"
print_kv "Auth Enabled" "$auth_enabled"

#------------------------------------------------------------------------------
# Verify Image Signatures
#
# Refuses to deploy any image that fails signature verification. Wraps
# scripts/security/verify-image.sh; failures abort before any az call.
#------------------------------------------------------------------------------

if [[ ! -x "$VERIFY_SCRIPT" ]]; then
  fatal "verify-image.sh not found or not executable: $VERIFY_SCRIPT"
fi

verify_signed_digest() {
  local label="$1" image_ref="$2"
  section "Verifying $label Signature"
  info "Verifying $image_ref"
  local args=(--image "$image_ref" --mode "$verify_mode")
  [[ "$offline" == "true" ]] && args+=(--offline)
  [[ -n "$trusted_root" ]] && args+=(--trusted-root "$trusted_root")
  [[ -n "$policy_file" ]] && args+=(--policy-file "$policy_file")
  [[ "$accept_public_rekor" == "true" ]] && args+=(--accept-public-rekor)
  if ! "$VERIFY_SCRIPT" "${args[@]}"; then
    fatal "$label image signature verification failed; refusing to deploy $image_ref"
  fi
}

if [[ "$skip_backend" == "false" ]]; then
  verify_signed_digest "Backend" "$backend_image"
fi
if [[ "$skip_frontend" == "false" ]]; then
  verify_signed_digest "Frontend" "$frontend_image"
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
# Update Container Apps (immutable digest references)
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

  info "Creating client secret for Easy Auth..."
  client_secret=$(az ad app credential reset \
    --id "$entra_client_id" \
    --display-name "easy-auth" \
    --years 2 \
    --query password -o tsv)

  info "Enabling ID token issuance..."
  az ad app update --id "$entra_client_id" \
    --enable-id-token-issuance true \
    --output none

  frontend_fqdn=$(az containerapp show \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --query 'properties.configuration.ingress.fqdn' -o tsv)

  info "Adding Easy Auth callback redirect URI..."
  az ad app update --id "$entra_client_id" \
    --web-redirect-uris "https://${frontend_fqdn}/.auth/login/aad/callback" \
    --output none

  info "Configuring Easy Auth Microsoft provider..."
  az containerapp auth microsoft update \
    --name "$frontend_app" \
    --resource-group "$rg" \
    --client-id "$entra_client_id" \
    --client-secret "$client_secret" \
    --issuer "https://login.microsoftonline.com/${entra_tenant_id}/v2.0" \
    --yes \
    --output none

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
print_kv "Backend Image" "${skip_backend:+<skipped>}${skip_backend:-$backend_image}"
print_kv "Frontend Image" "${skip_frontend:+<skipped>}${skip_frontend:-$frontend_image}"
print_kv "Backend App" "$backend_app"
print_kv "Frontend App" "$frontend_app"
print_kv "Verify Mode" "$verify_mode"
print_kv "Offline" "$offline"
print_kv "Update" "$([[ "$skip_update" == "true" ]] && echo 'Skipped' || echo 'Complete')"
print_kv "Easy Auth" "$([[ "$auth_enabled" == "true" ]] && echo 'Configured' || echo 'Disabled')"
[[ -n "$frontend_url" ]] && print_kv "Frontend URL" "$frontend_url"
info "Deployment complete"
