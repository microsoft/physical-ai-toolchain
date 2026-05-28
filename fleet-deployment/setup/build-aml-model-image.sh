#!/usr/bin/env bash
# Build inference image from a registered AzureML model and push to the designated ACR.
# Supports same-tenant (default) and cross-tenant (AML in tenant A, ACR in tenant B) topologies.
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

Download a registered AzureML model, build a container image into the designated
Azure Container Registry, sign it (Sigstore | Notation | none), and self-verify
the signature so the image will admit on signing-enforced clusters.

Supports same-tenant (AML and ACR in the same Entra tenant; default) and
cross-tenant (AML in tenant A, ACR in tenant B) topologies behind one input
contract.

REQUIRED RBAC PER TENANT:
  AML tenant: AzureML Data Scientist on workspace + Storage Blob Data Reader
              on workspace storage account.
  ACR tenant (ABAC mode):     Container Registry Repository Writer + Container
                              Registry Tasks Contributor on the ACR.
  ACR tenant (non-ABAC mode): AcrPush + Microsoft.ContainerRegistry/registries/
                              scheduleRun/action (or Contributor on the ACR).
  AKV (notation only):        Key Vault Certificates Officer + Key Vault Crypto User.

For cross-tenant: run 'az login --tenant <id>' for EACH tenant before invoking
this script. The script detects missing logins and instructs.

OPTIONS:
    -h, --help                   Show this help
    --tf-dir DIR                 Terraform directory (default: $DEFAULT_TF_DIR)
                                 Pass 'none' or any non-existent path to disable
                                 Terraform discovery (Terraform-optional mode);
                                 all required values must then come from CLI
                                 flags or DEFAULT_* env vars.
    --model-name NAME            AzureML registered model name (REQUIRED)
    --model-version VERSION      Model version, or 'latest' (default: latest)
    --image-repo REPO            ACR repository (default: <model-name>)
    --image-tag TAG              Image tag (default: <model-version>-sha-<git-short>)
    --dockerfile PATH            Dockerfile path (default: $DEFAULT_DOCKERFILE)
    --aml-subscription ID        AML subscription   (default: Terraform subscription_id)
    --aml-tenant ID              AML tenant         (default: Terraform tenant_id)
    --aml-rg NAME                AML resource group (default: Terraform resource_group)
    --aml-workspace NAME         AML workspace name (default: Terraform azureml_workspace)
    --acr-subscription ID        ACR subscription   (default: Terraform subscription_id;
                                 REQUIRED in cross-tenant or no-Terraform mode)
    --acr-tenant ID              ACR tenant         (default: Terraform tenant_id;
                                 REQUIRED in cross-tenant or no-Terraform mode)
    --acr-name NAME              ACR name           (default: Terraform container_registry;
                                 REQUIRED in cross-tenant or no-Terraform mode)
    --verify-mode MODE           Signing mode: sigstore | notation | none
                                 (default: signing_mode.value Terraform output
                                  or DEFAULT_VERIFY_MODE; falls back to 'none'
                                  with a warning when verify-image.sh is absent)
    --akv-key-id URI             AKV signing key URI (notation mode; default:
                                 Terraform notation_akv.value.signing_key_uri)
    --akv-tenant ID              AKV tenant       (default: same as ACR tenant)
    --akv-subscription ID        AKV subscription (default: same as ACR subscription)
    --vex-file PATH              OpenVEX statement to attach (sigstore only,
                                 default: $DEFAULT_VEX_FILE if present)
    --skip-attestations          Skip SBOM/VEX attestation steps (signing still runs)
    --skip-self-verify           Skip the verify-image.sh self-check
    --config-preview             Print configuration and exit

INFERENCE BASE IMAGE OVERRIDE:
    The inference base image is not exposed as a CLI flag. Override via:
      export INFERENCE_BASE_IMAGE=mcr.microsoft.com/azureml/minimal-py312-inference:1.0
    or by editing defaults.conf (DEFAULT_INFERENCE_BASE_IMAGE).

RESOLUTION ORDER (per value):
    Terraform output  >  CLI flag  >  DEFAULT_* env var  >  defaults.conf  >  fatal

A missing 'terraform.tfstate' is non-fatal; the script warns and falls through.
Pass --tf-dir none to disable Terraform discovery and let CLI/env values win.

EXAMPLES:
    # Same-tenant (typical), Terraform discovers everything:
    $(basename "$0") --model-name lerobot-act-pickplace

    # Same-tenant, explicit version + tag:
    $(basename "$0") --model-name lerobot-act-pickplace --model-version 7 \\
      --image-repo fleet/lerobot-act --image-tag 7-sha-abc1234

    # Cross-tenant: AML side from Terraform, ACR side from flags.
    $(basename "$0") --model-name lerobot-act-pickplace \\
      --acr-tenant cccc-dddd --acr-subscription <id> --acr-name acrfleetprod001

    # Fully CLI-driven (no Terraform state, e.g., support engineer):
    $(basename "$0") --tf-dir none --model-name lerobot-act-pickplace \\
      --aml-subscription <id> --aml-tenant <id> --aml-rg <rg> --aml-workspace <ws> \\
      --acr-subscription <id> --acr-tenant <id> --acr-name acrfleetprod001

    # Production (Notation + AKV):
    $(basename "$0") --model-name lerobot-act-pickplace --verify-mode notation
EOF
}

# CLI defaults — empty so the resolver can distinguish "unset" from "intentionally blank".
# DEFAULT_TF_DIR (from defaults.conf) is typically a relative path; anchor it to SCRIPT_DIR
# below alongside dockerfile / vex_file so discovery is independent of caller cwd.
tf_dir="${DEFAULT_TF_DIR:-../../infrastructure/terraform}"
model_name=""
model_version=""
image_repo=""
image_tag=""
dockerfile=""
aml_subscription=""
aml_tenant=""
aml_rg=""
aml_workspace=""
acr_subscription=""
acr_tenant=""
acr_name_override=""
verify_mode=""
akv_key_uri=""
akv_tenant=""
akv_subscription=""
vex_file=""
skip_attestations=false
skip_self_verify=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    --tf-dir)             tf_dir="$2"; shift 2 ;;
    --model-name)         model_name="$2"; shift 2 ;;
    --model-version)      model_version="$2"; shift 2 ;;
    --image-repo)         image_repo="$2"; shift 2 ;;
    --image-tag)          image_tag="$2"; shift 2 ;;
    --dockerfile)         dockerfile="$2"; shift 2 ;;
    --aml-subscription)   aml_subscription="$2"; shift 2 ;;
    --aml-tenant)         aml_tenant="$2"; shift 2 ;;
    --aml-rg)             aml_rg="$2"; shift 2 ;;
    --aml-workspace)      aml_workspace="$2"; shift 2 ;;
    --acr-subscription)   acr_subscription="$2"; shift 2 ;;
    --acr-tenant)         acr_tenant="$2"; shift 2 ;;
    --acr-name)           acr_name_override="$2"; shift 2 ;;
    --verify-mode)        verify_mode="$2"; shift 2 ;;
    --akv-key-id)         akv_key_uri="$2"; shift 2 ;;
    --akv-tenant)         akv_tenant="$2"; shift 2 ;;
    --akv-subscription)   akv_subscription="$2"; shift 2 ;;
    --vex-file)           vex_file="$2"; shift 2 ;;
    --skip-attestations)  skip_attestations=true; shift ;;
    --skip-self-verify)   skip_self_verify=true; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

# Apply non-resolver defaults (these aren't Terraform-discoverable).
model_version="${model_version:-${DEFAULT_AML_MODEL_VERSION:-latest}}"
inference_base_image="${INFERENCE_BASE_IMAGE:-${DEFAULT_INFERENCE_BASE_IMAGE:-mcr.microsoft.com/azureml/minimal-py312-inference:latest}}"

# Resolve dockerfile / vex_file / tf_dir against SCRIPT_DIR when defaults.conf supplies a
# relative path. realpath -m canonicalizes the result (collapses '..' segments) and tolerates
# non-existent paths so display stays clean for previews and fresh checkouts alike.
resolve_path() {
  local input="$1" fallback="$2"
  local path="${input:-$fallback}"
  if [[ "$path" == /* ]]; then
    realpath -m "$path"
  else
    realpath -m "$SCRIPT_DIR/${path#./}"
  fi
}
dockerfile="$(resolve_path "$dockerfile" "${DEFAULT_DOCKERFILE:-./Dockerfile.inference}")"
vex_file="$(resolve_path "$vex_file" "${DEFAULT_VEX_FILE:-../../security/vex/inference-base.openvex.json}")"
# Anchor relative tf_dir to SCRIPT_DIR so Terraform discovery is independent of caller cwd.
# 'none' is the documented sentinel for Terraform-optional mode and must not be resolved.
if [[ "$tf_dir" != "none" && "$tf_dir" != /* ]]; then
  tf_dir="$(resolve_path "$tf_dir" "")"
fi

# 'terraform' is only required when Terraform discovery is in use; resolved later in this section.
require_tools az jq git
require_az_extension ml

model_name="${model_name:-${DEFAULT_AML_MODEL_NAME:-}}"
[[ -n "$model_name" ]] || fatal "--model-name is required (or set DEFAULT_AML_MODEL_NAME)"

# image_repo defaults to model_name (one ACR repo per model_name; see Resolved Decision #16).
# Override via --image-repo or DEFAULT_IMAGE_REPO when sharing one repo across multiple models.
image_repo="${image_repo:-${DEFAULT_IMAGE_REPO:-$model_name}}"
[[ -f "$dockerfile" ]] || fatal "Dockerfile not found: $dockerfile"

#------------------------------------------------------------------------------
# Helper: non-fatal Terraform discovery.
# Returns '{}' (and warns) when state is missing so the resolver below can fall
# through to env vars / defaults.conf / fatal-if-still-empty.
#------------------------------------------------------------------------------
read_terraform_outputs_optional() {
  local td="$1"
  if [[ -z "$td" || "$td" == "none" || ! -d "$td" || ! -f "$td/terraform.tfstate" ]]; then
    warn "Terraform state not found at '${td:-<unset>}'; running in Terraform-optional mode."
    warn "Required values must come from CLI flags, DEFAULT_* env vars, or defaults.conf."
    echo '{}'
    return 0
  fi
  (cd "$td" && terraform output -json) || { warn "terraform output -json failed; treating as empty."; echo '{}'; }
}

#------------------------------------------------------------------------------
# Helper: layered value resolver (TF output > CLI > env > literal > optional fatal).
# Terraform output wins when present; CLI/env/literal fill the gap when it isn't.
# An optional secondary tf_key allows defensive fallback to a related output (e.g.
# tenant_id falling back to ml_workload_identity.value.tenant_id while the top-level
# output is missing).
# resolve_value <description> <cli> <env> <tf_json> <tf_key> <literal> <required> [tf_key_fallback]
#------------------------------------------------------------------------------
resolve_value() {
  local desc="$1" cli="$2" env="$3" tf_json="$4" tf_key="$5" lit="$6" required="$7" tf_key_fallback="${8:-}"
  local val=""
  if [[ -n "$tf_key" ]]; then
    val=$(tf_get "$tf_json" "$tf_key" "")
  fi
  if [[ -z "$val" && -n "$tf_key_fallback" ]]; then
    val=$(tf_get "$tf_json" "$tf_key_fallback" "")
  fi
  [[ -z "$val" ]] && val="$cli"
  [[ -z "$val" ]] && val="$env"
  [[ -z "$val" ]] && val="$lit"
  if [[ -z "$val" && "$required" == "true" ]]; then
    fatal "$desc not supplied. Provide via Terraform output ($tf_key), CLI flag, DEFAULT_* env var, or defaults.conf."
  fi
  echo "$val"
}

#------------------------------------------------------------------------------
# Helper: switch active subscription; instruct on missing tenant login.
#------------------------------------------------------------------------------
set_active_subscription() {
  local sub="$1" tenant="$2" purpose="$3"
  if ! az account set --subscription "$sub" 2>/dev/null; then
    error "Subscription '$sub' (tenant $tenant) not in az session for $purpose."
    fatal "Run: az login --tenant '$tenant'   then re-run this script."
  fi
}

#------------------------------------------------------------------------------
# Gather Configuration (Terraform-optional, layered resolution)
#------------------------------------------------------------------------------
section "Configuration"
tf_output=$(read_terraform_outputs_optional "$tf_dir")
tf_available=true
[[ "$tf_output" == "{}" ]] && tf_available=false

# When Terraform discovery is active we need 'terraform' on PATH (already invoked above).
if [[ "$tf_available" == "true" ]]; then
  require_tools terraform
fi

# Required AML-side values.
aml_subscription=$(resolve_value "AML subscription"   "$aml_subscription" "${DEFAULT_AML_SUBSCRIPTION_ID:-}" "$tf_output" "subscription_id.value"        "" true)
aml_tenant=$(resolve_value       "AML tenant"         "$aml_tenant"       "${DEFAULT_AML_TENANT_ID:-}"       "$tf_output" "tenant_id.value"               "" true "ml_workload_identity.value.tenant_id")
aml_rg=$(resolve_value           "AML resource group" "$aml_rg"           "${DEFAULT_AML_RESOURCE_GROUP:-}"  "$tf_output" "resource_group.value.name"     "" true)
aml_workspace=$(resolve_value    "AML workspace name" "$aml_workspace"    "${DEFAULT_AML_WORKSPACE_NAME:-}"  "$tf_output" "azureml_workspace.value.name"  "" true)

# Required ACR-side values. In hybrid mode the operator typically supplies these via flags;
# in same-tenant mode they fall through to the Terraform-discovered project ACR.
acr_subscription=$(resolve_value "ACR subscription" "$acr_subscription"  "${DEFAULT_ACR_SUBSCRIPTION_ID:-}" "$tf_output" "subscription_id.value"          "" true)
acr_tenant=$(resolve_value       "ACR tenant"       "$acr_tenant"        "${DEFAULT_ACR_TENANT_ID:-}"       "$tf_output" "tenant_id.value"                "" true "ml_workload_identity.value.tenant_id")
acr_name=$(resolve_value         "ACR name"         "$acr_name_override" "${DEFAULT_ACR_NAME:-}"            "$tf_output" "container_registry.value.name"  "" true)
acr_login_server=$(tf_get "$tf_output" "container_registry.value.login_server" "${acr_name}.azurecr.io")

# Signing-mode resolution. Same resolver but collapses to 'none' when verify-image.sh is absent.
verify_mode=$(resolve_value "Verify mode" "$verify_mode" "${DEFAULT_VERIFY_MODE:-}" "$tf_output" "signing_mode.value" "sigstore" true)
case "$verify_mode" in
  sigstore|notation|none) ;;
  *) fatal "Invalid --verify-mode: $verify_mode (expected sigstore | notation | none)" ;;
esac
if [[ "$verify_mode" == "sigstore" && ! -f "$REPO_ROOT/scripts/security/verify-image.sh" ]]; then
  warn "scripts/security/verify-image.sh not found (PR #592 not merged?); forcing verify_mode=none"
  verify_mode="none"
fi

# Notation-only values: AKV tenant/subscription default to ACR's; AKV key URI required when notation selected.
akv_tenant=$(resolve_value       "AKV tenant"         "$akv_tenant"       "${DEFAULT_AKV_TENANT_ID:-}"       "$tf_output" "notation_akv.value.tenant_id"        "$acr_tenant"       false)
akv_subscription=$(resolve_value "AKV subscription"   "$akv_subscription" "${DEFAULT_AKV_SUBSCRIPTION_ID:-}" "$tf_output" "notation_akv.value.subscription_id"  "$acr_subscription" false)
if [[ "$verify_mode" == "notation" ]]; then
  akv_key_uri=$(resolve_value "AKV signing key URI" "$akv_key_uri" "${DEFAULT_AKV_KEY_URI:-}" "$tf_output" "notation_akv.value.signing_key_uri" "" true)
fi

# Resolve 'latest' against the live AML workspace (one round-trip).
# Skip the lookup under --config-preview so the script never contacts Azure in preview mode
# (Plan Success Criterion #2). The display value below preserves operator awareness.
model_version_display="$model_version"
if [[ "$model_version" == "latest" ]]; then
  if [[ "$config_preview" == "true" ]]; then
    info "Skipping AML latest-version lookup in --config-preview mode"
    model_version_display="latest (resolves at runtime)"
  else
    set_active_subscription "$aml_subscription" "$aml_tenant" "AML latest-version lookup"
    model_version=$(az ml model list \
      --name "$model_name" \
      --resource-group "$aml_rg" \
      --workspace-name "$aml_workspace" \
      --query "[0].version" -o tsv 2>/dev/null) \
      || fatal "Could not resolve latest version for model '$model_name'"
    [[ -n "$model_version" ]] || fatal "No versions found for model '$model_name' in workspace '$aml_workspace'"
    model_version_display="$model_version (latest)"
  fi
fi

# Compute image_tag default — '<model_version>-sha-<git_short>' for FluxCD ImagePolicy
# compatibility (per-model-name repo; Resolved Decision #17). Falls back to a UTC-timestamped
# tag outside a git repo so the build is still uniquely identifiable.
if [[ -z "$image_tag" ]]; then
  if git_short=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null); then
    image_tag="${model_version}-sha-${git_short}"
  else
    image_tag="${model_version}-build-$(date -u +%Y%m%d%H%M%S)"
  fi
fi
image_ref="${image_repo}:${image_tag}"

if [[ "$aml_tenant" == "$acr_tenant" ]]; then
  topology="same-tenant"
else
  topology="cross-tenant"
fi

print_kv "Topology"            "$topology"
print_kv "Terraform Discovery" "$([[ "$tf_available" == true ]] && echo "$tf_dir" || echo "disabled (state not found)")"
print_kv "AML Subscription"    "$aml_subscription"
print_kv "AML Tenant"          "$aml_tenant"
print_kv "AML Resource Group"  "$aml_rg"
print_kv "AML Workspace"       "$aml_workspace"
print_kv "Model"               "$model_name"
print_kv "Model Version"       "$model_version_display"
print_kv "ACR Subscription"    "$acr_subscription"
print_kv "ACR Tenant"          "$acr_tenant"
print_kv "ACR Name"            "$acr_name"
print_kv "ACR Login Server"    "$acr_login_server"
print_kv "Image Repo"          "$image_repo"
print_kv "Image Tag"           "$image_tag"
print_kv "Image Reference"     "${acr_login_server}/${image_ref}"
print_kv "Dockerfile"          "$dockerfile"
print_kv "Base Image"          "$inference_base_image"
print_kv "Verify Mode"         "$verify_mode"
print_kv "VEX File"            "$([[ -f "$vex_file" ]] && echo "$vex_file" || echo "(not present)")"
if [[ "$verify_mode" == "notation" ]]; then
  print_kv "AKV Tenant"        "$akv_tenant"
  print_kv "AKV Subscription"  "$akv_subscription"
  print_kv "AKV Key URI"       "$akv_key_uri"
fi

if [[ "$config_preview" == "true" ]]; then
  info "Config preview mode — exiting without changes."
  exit 0
fi

#------------------------------------------------------------------------------
# Download Registered Model (AML tenant)
#
# Two-directory pattern:
#   $download_dir       — mktemp parent (cleanup root, single trap)
#   $download_dir/build — assembled Docker build context (Dockerfile + model/)
# Avoids relying on 'az ml model download' tolerance for a non-empty
# --download-path and preserves the original artifact tree if the rename fails.
#------------------------------------------------------------------------------
section "Download Registered Model"
set_active_subscription "$aml_subscription" "$aml_tenant" "AML model download"

download_dir=$(mktemp -d -t aml-model-XXXXXX)
build_ctx="$download_dir/build"
mkdir -p "$build_ctx"
trap 'rm -rf "$download_dir"' EXIT

cp "$dockerfile" "$build_ctx/Dockerfile"

az ml model download \
  --name "$model_name" \
  --version "$model_version" \
  --resource-group "$aml_rg" \
  --workspace-name "$aml_workspace" \
  --download-path "$download_dir"

# Materialize the model under the build context so 'COPY model/ /opt/model/' resolves.
[[ -d "$download_dir/$model_name" ]] || fatal "az ml model download did not produce $download_dir/$model_name"
mv "$download_dir/$model_name" "$build_ctx/model"
[[ -d "$build_ctx/model" ]] || fatal "Build context model/ directory missing after move"
if [[ -z "$(ls -A "$build_ctx/model")" ]]; then
  fatal "Build context model/ directory is empty (no artifacts downloaded)"
fi

# ACR Tasks source-upload limit is 100 MB; fail fast with an actionable error.
ctx_bytes=$(du -sb "$build_ctx" | awk '{print $1}')
if (( ctx_bytes > 104857600 )); then
  fatal "Build context size ${ctx_bytes} bytes exceeds 100 MB ACR Tasks upload limit. Consider model splitting or pre-publishing as OCI artifact."
fi

#------------------------------------------------------------------------------
# Build and Push to ACR (ACR tenant)
#------------------------------------------------------------------------------
section "Build and Push to ACR"
set_active_subscription "$acr_subscription" "$acr_tenant" "ACR build/push"

# az acr build authenticates via the active az session and runs server-side in
# ACR Tasks. No local docker daemon, no prior 'az acr login' required.
az acr build \
  --registry "$acr_name" \
  --image "$image_ref" \
  --file "$build_ctx/Dockerfile" \
  --build-arg "BASE_IMAGE=${inference_base_image}" \
  --build-arg "MODEL_NAME=${model_name}" \
  --build-arg "MODEL_VERSION=${model_version}" \
  "$build_ctx"

# Resolve the immutable digest — the only reference admission policies trust.
digest=$(az acr repository show \
  --name "$acr_name" \
  --image "$image_ref" \
  --query 'digest' -o tsv)
[[ -n "$digest" ]] || fatal "Failed to resolve digest for ${image_ref}"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fatal "Unexpected digest shape from az acr repository show: $digest"

image_digest_ref="${acr_login_server}/${image_repo}@${digest}"
image_tag_ref="${acr_login_server}/${image_ref}"

#------------------------------------------------------------------------------
# Sign Image (verify_mode-aware; tenant-aware for notation+AKV)
#------------------------------------------------------------------------------
section "Sign Image"
info "Signing $image_digest_ref using $verify_mode mode"

case "$verify_mode" in
  sigstore)
    require_tools cosign
    [[ "$skip_attestations" == "true" ]] || require_tools syft
    # Local-mode safety guard: developer Entra identity won't satisfy production Kyverno.
    if [[ -z "${GITHUB_ACTIONS:-}" && -z "${TF_BUILD:-}" ]]; then
      warn "Signing locally with sigstore: certificate will bind to your developer Entra identity."
      warn "Production Kyverno policies will reject this image. CI is required for production builds."
    fi
    set_active_subscription "$acr_subscription" "$acr_tenant" "ACR signing context"
    az acr login --name "$acr_name"
    cosign sign --yes "$image_digest_ref"
    if [[ "$skip_attestations" != "true" ]]; then
      syft "$image_digest_ref" -o spdx-json > "$build_ctx/sbom.spdx.json"
      cosign attest --yes --predicate "$build_ctx/sbom.spdx.json" --type spdxjson "$image_digest_ref"
      if [[ -f "$vex_file" ]]; then
        cosign attest --yes --predicate "$vex_file" --type openvex "$image_digest_ref"
      else
        warn "VEX file not present at '$vex_file' — skipping OpenVEX attestation."
      fi
    fi
    ;;

  notation)
    require_tools notation oras
    [[ "$skip_attestations" == "true" ]] || require_tools syft
    [[ -n "$akv_key_uri" ]] || fatal "--akv-key-id (or DEFAULT_AKV_KEY_URI) is required for notation mode"
    # Step 1: cache ACR Docker token while context is in the ACR tenant.
    set_active_subscription "$acr_subscription" "$acr_tenant" "ACR signing context"
    az acr login --name "$acr_name"
    # Step 2: switch to the AKV tenant so the azure-kv plugin reads the right Entra token.
    # Skip the switch when AKV lives in the ACR tenant (the common single-tenant default)
    # to avoid redundant 'az account set' calls.
    if [[ "$akv_tenant" != "$acr_tenant" ]]; then
      set_active_subscription "$akv_subscription" "$akv_tenant" "notation sign (AKV)"
    else
      info "AKV in same tenant as ACR; skipping context switch"
    fi
    notation sign \
      --plugin azure-kv \
      --plugin-config self_signed=false \
      --plugin-config credential_type=azurecli \
      --id "$akv_key_uri" \
      "$image_digest_ref"
    # Step 3: switch back to the ACR tenant so SBOM oras attach uses the ACR token.
    # Same guard — no-op when AKV and ACR share a tenant.
    if [[ "$akv_tenant" != "$acr_tenant" ]]; then
      set_active_subscription "$acr_subscription" "$acr_tenant" "ACR oras attach"
    fi
    if [[ "$skip_attestations" != "true" ]]; then
      # Notation has no native attestation primitive; emit SBOM as separate OCI referrer.
      # OpenVEX is NOT attached in notation mode (--vex-file is sigstore-only).
      syft "$image_digest_ref" -o spdx-json > "$build_ctx/sbom.spdx.json"
      oras attach \
        --artifact-type application/vnd.spdx+json \
        "$image_digest_ref" \
        "$build_ctx/sbom.spdx.json:application/spdx+json"
    fi
    ;;

  none)
    warn "verify_mode=none: skipping image signing. Image will be REJECTED by Kyverno-enforcing clusters."
    ;;
esac

#------------------------------------------------------------------------------
# Self-Verify Before Hand-Off
#------------------------------------------------------------------------------
if [[ "$skip_self_verify" == "true" ]]; then
  info "Self-verify skipped per --skip-self-verify"
elif [[ "$verify_mode" != "none" ]]; then
  section "Self-Verify Signature"
  if [[ -x "$REPO_ROOT/scripts/security/verify-image.sh" ]]; then
    "$REPO_ROOT/scripts/security/verify-image.sh" \
      --mode "$verify_mode" \
      --image "$image_digest_ref" \
      || fatal "Signature self-verification failed; image would be rejected at admission"
  else
    warn "scripts/security/verify-image.sh not present (PR #592 not merged yet) — skipping self-verify."
  fi
fi

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Topology"                  "$topology"
print_kv "Model"                     "${model_name}@v${model_version}"
print_kv "Image (digest)"            "$image_digest_ref"
print_kv "Image (tag)"               "$image_tag_ref"
print_kv "Verify Mode"               "$verify_mode"
print_kv "Build Context (transient)" "$build_ctx"
info "Done."
