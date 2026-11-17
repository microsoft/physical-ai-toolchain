#!/usr/bin/env bash
# Build a policy carrier image from a registered model or a checkpoint directory and push it to Azure
# Container Registry or the local profile's registry.
# Azure builds support same-tenant (default) and cross-tenant (AML in tenant A, ACR in tenant B) topologies.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Build the policy carrier image from a model and push it to a registry. The carrier
copies the model to /policy/. On the default 'scratch' base it holds only the model,
and consumer pods mount it through an OCI image volume. On a runtime base
(--base-image) it runs as the consumer's own image, as OSMO evaluation tasks do.

The platform profile selects the model registry and the image registry:
  azure (default)  A registered AzureML model, pushed to Azure Container Registry,
                   signed (Sigstore | Notation | none), and self-verified so the
                   image admits on signing-enforced clusters.
  local            A model from the local MLflow registry, pushed to the local
                   registry that K3s pulls from. Signing is skipped because only
                   this host pulls from that registry.
With --model-path, either profile packages a checkpoint directory instead.

Azure builds support same-tenant (AML and ACR in the same Entra tenant; default)
and cross-tenant (AML in tenant A, ACR in tenant B) topologies behind one input
contract.

REQUIRED RBAC PER TENANT (AzureML models and ACR pushes):
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
    --profile NAME               Platform profile: azure (default) or local (PLATFORM_PROFILE)
    --model-source SOURCE        Model registry: azureml | mlflow | path (default: the
                                 profile's tracking backend, or path with --model-path)
    --model-name NAME            Registered model name, and the default image repository (REQUIRED)
    --model-version VERSION      Model version, or 'latest' (default: latest; 'local' for path)
    --model-path DIR             Checkpoint directory to package instead of a registered model
    --mlflow-tracking-uri URI    MLflow server for the mlflow source (default: the local
                                 profile's host URI, or MLFLOW_TRACKING_URI)
    --base-image IMAGE           Carrier base: scratch for a weights-only carrier, or a
                                 runtime image for a runnable carrier
                                 (default: INFERENCE_BASE_IMAGE, then $DEFAULT_INFERENCE_BASE_IMAGE)
    --image-repo REPO            Image repository (default: <model-name>)
    --image-tag TAG              Image tag (default: <model-version>-sha-<git-short>)
    --dockerfile PATH            Dockerfile path (default: $DEFAULT_DOCKERFILE)
    --platform PLATFORM          Target platform for buildx (default: $DEFAULT_IMAGE_PLATFORM)
                                 Common values: linux/amd64, linux/arm64
    --config-preview             Print configuration and exit

AZURE OPTIONS (AzureML models and ACR pushes):
    --tf-dir DIR                 Terraform directory (default: $DEFAULT_TF_DIR)
                                 Pass 'none' or any non-existent path to disable
                                 Terraform discovery (Terraform-optional mode);
                                 all required values must then come from CLI
                                 flags or DEFAULT_* env vars.
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
    --verify-mode MODE           ACR signing mode: sigstore | notation | none
                                 (default: signing_mode.value Terraform output
                                  or DEFAULT_VERIFY_MODE; falls back to 'none'
                                  with a warning when verify-image.sh is absent)
    --akv-key-id URI             AKV signing key URI (notation mode; default:
                                 Terraform notation_akv.value.signing_key_uri)
    --akv-tenant ID              AKV tenant       (default: same as ACR tenant)
    --akv-subscription ID        AKV subscription (default: same as ACR subscription)
    --skip-self-verify           Skip the verify-image.sh self-check

ATTESTATIONS:
    SBOM and OpenVEX attestations for ACR images are produced by a separate
    script so security/compliance can refresh them without rebuilding. After
    this script completes, run:
      fleet-deployment/setup/attest-image.sh --image <digest-ref>

RESOLUTION ORDER (per Azure value):
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

    # Local profile: the latest MLflow version into the local registry
    $(basename "$0") --profile local --model-name lerobot-act-pickplace

    # Local profile: a runnable carrier that OSMO runs as the evaluation task image
    $(basename "$0") --profile local --model-name lerobot-act-pickplace \\
      --base-image <evaluation-runtime-image>
EOF
}

# CLI defaults — empty so the resolver can distinguish "unset" from "intentionally blank".
# DEFAULT_TF_DIR (from defaults.conf) is typically a relative path; anchor it to SCRIPT_DIR
# below alongside dockerfile so discovery is independent of caller cwd.
tf_dir="${DEFAULT_TF_DIR:-../../infrastructure/terraform}"
profile=""
model_source=""
model_name=""
model_version=""
model_path=""
mlflow_tracking_uri=""
base_image=""
image_repo=""
image_tag=""
dockerfile=""
platform=""
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
skip_self_verify=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    --profile)              profile="$2"; shift 2 ;;
    --model-source)         model_source="$2"; shift 2 ;;
    --model-name)           model_name="$2"; shift 2 ;;
    --model-version)        model_version="$2"; shift 2 ;;
    --model-path)           model_path="$2"; shift 2 ;;
    --mlflow-tracking-uri)  mlflow_tracking_uri="$2"; shift 2 ;;
    --base-image)           base_image="$2"; shift 2 ;;
    --tf-dir)               tf_dir="$2"; shift 2 ;;
    --image-repo)           image_repo="$2"; shift 2 ;;
    --image-tag)            image_tag="$2"; shift 2 ;;
    --dockerfile)           dockerfile="$2"; shift 2 ;;
    --platform)             platform="$2"; shift 2 ;;
    --aml-subscription)     aml_subscription="$2"; shift 2 ;;
    --aml-tenant)           aml_tenant="$2"; shift 2 ;;
    --aml-rg)               aml_rg="$2"; shift 2 ;;
    --aml-workspace)        aml_workspace="$2"; shift 2 ;;
    --acr-subscription)     acr_subscription="$2"; shift 2 ;;
    --acr-tenant)           acr_tenant="$2"; shift 2 ;;
    --acr-name)             acr_name_override="$2"; shift 2 ;;
    --verify-mode)          verify_mode="$2"; shift 2 ;;
    --akv-key-id)           akv_key_uri="$2"; shift 2 ;;
    --akv-tenant)           akv_tenant="$2"; shift 2 ;;
    --akv-subscription)     akv_subscription="$2"; shift 2 ;;
    --skip-self-verify)     skip_self_verify=true; shift ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Model Source and Image Target
#------------------------------------------------------------------------------

load_platform_profile "$profile"

# The profile's tracking backend is its model registry; a checkpoint directory needs none.
if [[ -z "$model_source" ]]; then
  if [[ -n "$model_path" ]]; then
    model_source="path"
  else
    case "$PROFILE_TRACKING_BACKEND" in
      azureml|mlflow) model_source="$PROFILE_TRACKING_BACKEND" ;;
      *) fatal "TRACKING_BACKEND=$PROFILE_TRACKING_BACKEND has no model registry; pass --model-path" ;;
    esac
  fi
fi
case "$model_source" in
  azureml|mlflow)
    [[ -z "$model_path" ]] || fatal "--model-path packages a directory; use it with --model-source path"
    ;;
  path)
    [[ -n "$model_path" ]] || fatal "--model-source path needs --model-path"
    [[ -d "$model_path" ]] || fatal "--model-path is not a directory: $model_path"
    model_path="$(realpath "$model_path")"
    model_version="${model_version:-local}"
    ;;
  *) fatal "Invalid --model-source: $model_source (expected azureml | mlflow | path)" ;;
esac

# The local profile's registry, or ACR when the profile has none.
target_registry="$PROFILE_IMAGE_REGISTRY"
uses_azure=false
if [[ "$model_source" == "azureml" || -z "$target_registry" ]]; then
  uses_azure=true
fi

# Apply non-resolver defaults (these aren't Terraform-discoverable).
model_version="${model_version:-${DEFAULT_AML_MODEL_VERSION:-latest}}"
inference_base_image="${base_image:-${INFERENCE_BASE_IMAGE:-${DEFAULT_INFERENCE_BASE_IMAGE:-scratch}}}"
platform="${platform:-${DEFAULT_IMAGE_PLATFORM:-linux/amd64}}"

# Resolve dockerfile / tf_dir against SCRIPT_DIR when defaults.conf supplies a
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
# Anchor relative tf_dir to SCRIPT_DIR so Terraform discovery is independent of caller cwd.
# 'none' is the documented sentinel for Terraform-optional mode and must not be resolved.
if [[ "$tf_dir" != "none" && "$tf_dir" != /* ]]; then
  tf_dir="$(resolve_path "$tf_dir" "")"
fi

# 'terraform' is only required when Terraform discovery is in use; resolved later in this section.
require_tools docker jq git
if [[ "$uses_azure" == "true" ]]; then
  require_tools az
fi
if [[ "$model_source" == "azureml" ]]; then
  require_az_extension ml
fi
if [[ "$model_source" == "mlflow" ]]; then
  require_tools uv curl
fi

model_name="${model_name:-${DEFAULT_AML_MODEL_NAME:-}}"
[[ -n "$model_name" ]] || fatal "--model-name is required (or set DEFAULT_AML_MODEL_NAME)"

# image_repo defaults to model_name (one repository per model_name; see Resolved Decision #16).
# Override via --image-repo or DEFAULT_IMAGE_REPO when sharing one repo across multiple models.
image_repo="${image_repo:-${DEFAULT_IMAGE_REPO:-$model_name}}"
[[ -f "$dockerfile" ]] || fatal "Dockerfile not found: $dockerfile"

if [[ "$model_source" == "mlflow" ]]; then
  # The host reaches the local MLflow server at its pinned ClusterIP; pods use the cluster DNS name.
  mlflow_tracking_uri="${mlflow_tracking_uri:-${PROFILE_MLFLOW_HOST_URI:-${MLFLOW_TRACKING_URI:-}}}"
  [[ -n "$mlflow_tracking_uri" ]] || fatal "The mlflow model source needs --mlflow-tracking-uri or MLFLOW_TRACKING_URI"
fi

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
tf_output='{}'
tf_available=false
if [[ "$uses_azure" == "true" ]]; then
  tf_output=$(read_terraform_outputs_optional "$tf_dir")
  [[ "$tf_output" != "{}" ]] && tf_available=true
fi

# When Terraform discovery is active we need 'terraform' on PATH (already invoked above).
if [[ "$tf_available" == "true" ]]; then
  require_tools terraform
fi

# AML-side values, required for AzureML models.
if [[ "$model_source" == "azureml" ]]; then
  aml_subscription=$(resolve_value "AML subscription"   "$aml_subscription" "${DEFAULT_AML_SUBSCRIPTION_ID:-}" "$tf_output" "subscription_id.value"        "" true)
  aml_tenant=$(resolve_value       "AML tenant"         "$aml_tenant"       "${DEFAULT_AML_TENANT_ID:-}"       "$tf_output" "tenant_id.value"               "" true "ml_workload_identity.value.tenant_id")
  aml_rg=$(resolve_value           "AML resource group" "$aml_rg"           "${DEFAULT_AML_RESOURCE_GROUP:-}"  "$tf_output" "resource_group.value.name"     "" true)
  aml_workspace=$(resolve_value    "AML workspace name" "$aml_workspace"    "${DEFAULT_AML_WORKSPACE_NAME:-}"  "$tf_output" "azureml_workspace.value.name"  "" true)
fi

if [[ -z "$target_registry" ]]; then
  # ACR-side values. In hybrid mode the operator typically supplies these via flags;
  # in same-tenant mode they fall through to the Terraform-discovered project ACR.
  acr_subscription=$(resolve_value "ACR subscription" "$acr_subscription"  "${DEFAULT_ACR_SUBSCRIPTION_ID:-}" "$tf_output" "subscription_id.value"          "" true)
  acr_tenant=$(resolve_value       "ACR tenant"       "$acr_tenant"        "${DEFAULT_ACR_TENANT_ID:-}"       "$tf_output" "tenant_id.value"                "" true "ml_workload_identity.value.tenant_id")
  acr_name=$(resolve_value         "ACR name"         "$acr_name_override" "${DEFAULT_ACR_NAME:-}"            "$tf_output" "container_registry.value.name"  "" true)
  registry_host=$(tf_get "$tf_output" "container_registry.value.login_server" "${acr_name}.azurecr.io")

  # Signing-mode resolution. Same resolver but collapses to 'none' when verify-image.sh is absent.
  verify_mode=$(resolve_value "Verify mode" "$verify_mode" "${DEFAULT_VERIFY_MODE:-}" "$tf_output" "signing_mode.value" "sigstore" true)
  case "$verify_mode" in
    sigstore|notation|none) ;;
    *) fatal "Invalid --verify-mode: $verify_mode (expected sigstore | notation | none)" ;;
  esac
  # Probe verify-image.sh once for both signing modes. Without it we cannot self-verify,
  # so downgrade to 'none' rather than ship an image we can't prove will admit.
  if [[ "$verify_mode" != "none" && ! -f "$REPO_ROOT/scripts/security/verify-image.sh" ]]; then
    warn "scripts/security/verify-image.sh not found (PR #592 not merged?); forcing verify_mode=none"
    verify_mode="none"
  fi

  # Notation-only values: AKV tenant/subscription default to ACR's; AKV key URI required when notation selected.
  akv_tenant=$(resolve_value       "AKV tenant"         "$akv_tenant"       "${DEFAULT_AKV_TENANT_ID:-}"       "$tf_output" "notation_akv.value.tenant_id"        "$acr_tenant"       false)
  akv_subscription=$(resolve_value "AKV subscription"   "$akv_subscription" "${DEFAULT_AKV_SUBSCRIPTION_ID:-}" "$tf_output" "notation_akv.value.subscription_id"  "$acr_subscription" false)
  if [[ "$verify_mode" == "notation" ]]; then
    akv_key_uri=$(resolve_value "AKV signing key URI" "$akv_key_uri" "${DEFAULT_AKV_KEY_URI:-}" "$tf_output" "notation_akv.value.signing_key_uri" "" true)
  fi
else
  registry_host="$target_registry"
  # Only this host pulls from the local registry, and the local profile enforces no signature policy.
  if [[ -n "$verify_mode" && "$verify_mode" != "none" ]]; then
    warn "Ignoring --verify-mode $verify_mode: images in $target_registry are not signed"
  fi
  verify_mode="none"
fi

# Resolve 'latest' against the model registry (one round-trip).
# Skip the lookup under --config-preview so the script never contacts a registry in preview mode
# (Plan Success Criterion #2). The display value below preserves operator awareness.
model_version_display="$model_version"
if [[ "$model_version" == "latest" ]]; then
  if [[ "$config_preview" == "true" ]]; then
    info "Skipping the latest-version lookup in --config-preview mode"
    model_version_display="latest (resolves at runtime)"
  elif [[ "$model_source" == "azureml" ]]; then
    set_active_subscription "$aml_subscription" "$aml_tenant" "AML latest-version lookup"
    model_version=$(az ml model list \
      --name "$model_name" \
      --resource-group "$aml_rg" \
      --workspace-name "$aml_workspace" \
      --query "[0].version" -o tsv 2>/dev/null) \
      || fatal "Could not resolve latest version for model '$model_name'"
    [[ -n "$model_version" ]] || fatal "No versions found for model '$model_name' in workspace '$aml_workspace'"
    model_version_display="$model_version (latest)"
  else
    registered_model=$(curl --fail --silent --show-error --get --data-urlencode "name=$model_name" \
      "$mlflow_tracking_uri/api/2.0/mlflow/registered-models/get") \
      || fatal "Could not read model '$model_name' from $mlflow_tracking_uri"
    model_version=$(jq -r '[.registered_model.latest_versions[]?.version | tonumber] | max // empty' <<< "$registered_model")
    [[ -n "$model_version" ]] || fatal "No versions found for model '$model_name' in $mlflow_tracking_uri"
    model_version_display="$model_version (latest)"
  fi
fi

# Compute image_tag default — '<model_version>-sha-<git_short>' for FluxCD ImagePolicy
# compatibility (per-model-name repo; Resolved Decision #17). Falls back to a UTC-timestamped
# tag outside a git repo so the build is still uniquely identifiable.
# In --config-preview with model_version=='latest' we cannot know the real version yet, so
# substitute a placeholder rather than print a misleading 'latest-sha-<git>' tag the build
# would never actually produce.
if [[ -z "$image_tag" ]]; then
  tag_version="$model_version"
  if [[ "$config_preview" == "true" && "$model_version" == "latest" ]]; then
    tag_version="<resolved-at-runtime>"
  fi
  if git_short=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null); then
    image_tag="${tag_version}-sha-${git_short}"
  else
    image_tag="${tag_version}-build-$(date -u +%Y%m%d%H%M%S)"
  fi
fi
image_ref="${image_repo}:${image_tag}"

topology="n/a"
if [[ "$model_source" == "azureml" && -z "$target_registry" ]]; then
  if [[ "$aml_tenant" == "$acr_tenant" ]]; then
    topology="same-tenant"
  else
    topology="cross-tenant"
  fi
fi

print_kv "Platform Profile"    "$PROFILE_NAME"
print_kv "Model Source"        "$model_source"
if [[ "$uses_azure" == "true" ]]; then
  print_kv "Topology"            "$topology"
  print_kv "Terraform Discovery" "$([[ "$tf_available" == true ]] && echo "$tf_dir" || echo "disabled (state not found)")"
fi
case "$model_source" in
  azureml)
    print_kv "AML Subscription"    "$aml_subscription"
    print_kv "AML Tenant"          "$aml_tenant"
    print_kv "AML Resource Group"  "$aml_rg"
    print_kv "AML Workspace"       "$aml_workspace"
    ;;
  mlflow) print_kv "MLflow Server" "$mlflow_tracking_uri" ;;
  path)   print_kv "Model Path" "$model_path" ;;
esac
print_kv "Model"               "$model_name"
print_kv "Model Version"       "$model_version_display"
if [[ -z "$target_registry" ]]; then
  print_kv "ACR Subscription"    "$acr_subscription"
  print_kv "ACR Tenant"          "$acr_tenant"
  print_kv "ACR Name"            "$acr_name"
  print_kv "ACR Login Server"    "$registry_host"
else
  print_kv "Registry"            "$registry_host (local profile)"
fi
print_kv "Image Repo"          "$image_repo"
print_kv "Image Tag"           "$image_tag"
print_kv "Image Reference"     "${registry_host}/${image_ref}"
print_kv "Dockerfile"          "$dockerfile"
print_kv "Platform"            "$platform"
print_kv "Base Image"          "$inference_base_image"
if [[ -n "$target_registry" ]]; then
  print_kv "Verify Mode"         "none (local registry; signing skipped)"
else
  print_kv "Verify Mode"         "$verify_mode"
fi
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
# Materialize the Model
#
# Two-directory pattern:
#   $download_dir       — mktemp parent (cleanup root, single trap)
#   $download_dir/build — assembled Docker build context (Dockerfile + model/)
# Avoids relying on download tools' tolerance for a non-empty destination and
# preserves the original artifact tree if the rename fails.
#------------------------------------------------------------------------------
download_dir=$(mktemp -d -t model-image-XXXXXX)
build_ctx="$download_dir/build"
mkdir -p "$build_ctx"
trap 'rm -rf "$download_dir"' EXIT

cp "$dockerfile" "$build_ctx/Dockerfile"

case "$model_source" in
  azureml)
    section "Download Registered Model"
    set_active_subscription "$aml_subscription" "$aml_tenant" "AML model download"
    az ml model download \
      --name "$model_name" \
      --version "$model_version" \
      --resource-group "$aml_rg" \
      --workspace-name "$aml_workspace" \
      --download-path "$download_dir"
    [[ -d "$download_dir/$model_name" ]] || fatal "az ml model download did not produce $download_dir/$model_name"
    mv "$download_dir/$model_name" "$build_ctx/model"
    ;;
  mlflow)
    section "Download Registered Model"
    # A model version's source is its checkpoint directory, which downloads as one directory.
    downloaded=$(MLFLOW_TRACKING_URI="$mlflow_tracking_uri" uv run --project "$REPO_ROOT" --frozen \
      mlflow artifacts download --artifact-uri "models:/${model_name}/${model_version}" \
      --dst-path "$download_dir/mlflow") \
      || fatal "Could not download models:/${model_name}/${model_version} from $mlflow_tracking_uri"
    downloaded="${downloaded##*$'\n'}"
    [[ -d "$downloaded" ]] || fatal "models:/${model_name}/${model_version} did not download as a directory"
    mv "$downloaded" "$build_ctx/model"
    ;;
  path)
    section "Copy Checkpoint"
    # Follow links so the build context holds the files themselves.
    cp -RL "$model_path" "$build_ctx/model"
    ;;
esac

# The Dockerfile copies model/ to /policy/.
[[ -d "$build_ctx/model" ]] || fatal "Build context model/ directory missing"
if [[ -z "$(ls -A "$build_ctx/model")" ]]; then
  fatal "Build context model/ directory is empty (no model artifacts)"
fi

#------------------------------------------------------------------------------
# Build and Push
#------------------------------------------------------------------------------
section "Build and Push"
if [[ -z "$target_registry" ]]; then
  set_active_subscription "$acr_subscription" "$acr_tenant" "ACR build/push"
  # Build locally and stream layers straight to ACR; bypasses the ~100 MB ACR Tasks
  # source-upload cap that blocks large baked-in models. 'az acr login' caches an ACR
  # Docker token in the local daemon's credential store so 'docker buildx --push' authenticates.
  az acr login --name "$acr_name"
fi

build_metadata_file="$download_dir/buildx-metadata.json"
docker buildx build \
  --push \
  --platform "$platform" \
  --tag "${registry_host}/${image_ref}" \
  --file "$build_ctx/Dockerfile" \
  --build-arg "BASE_IMAGE=${inference_base_image}" \
  --build-arg "MODEL_NAME=${model_name}" \
  --build-arg "MODEL_VERSION=${model_version}" \
  --build-arg "MODEL_SOURCE=${model_source}" \
  --metadata-file "$build_metadata_file" \
  "$build_ctx"

# Resolve the immutable digest from the buildx metadata file — the only reference
# admission policies trust. 'containerimage.digest' is the manifest digest of the
# image that THIS build just pushed, so it's race-free against parallel pushes to
# the same tag.
digest=$(jq -r '."containerimage.digest" // empty' "$build_metadata_file" 2>/dev/null || true)
[[ -n "$digest" ]] || fatal "Failed to resolve digest from buildx metadata file: $build_metadata_file"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || fatal "Unexpected digest shape: $digest"

image_digest_ref="${registry_host}/${image_repo}@${digest}"
image_tag_ref="${registry_host}/${image_ref}"

#------------------------------------------------------------------------------
# Sign Image (verify_mode-aware; tenant-aware for notation+AKV)
#------------------------------------------------------------------------------
section "Sign Image"
if [[ -n "$target_registry" ]]; then
  info "Signing skipped: only this host's K3s pulls from $target_registry, and the local profile enforces no signature policy"
else
  info "Signing $image_digest_ref using $verify_mode mode"
fi

case "$verify_mode" in
  sigstore)
    require_tools cosign
    # Local-mode safety guard: developer Entra identity won't satisfy production Kyverno.
    if [[ -z "${GITHUB_ACTIONS:-}" && -z "${TF_BUILD:-}" ]]; then
      warn "Signing locally with sigstore: certificate will bind to your developer Entra identity."
      warn "Production Kyverno policies will reject this image. CI is required for production builds."
    fi
    set_active_subscription "$acr_subscription" "$acr_tenant" "ACR signing context"
    az acr login --name "$acr_name"
    cosign sign --yes "$image_digest_ref"
    ;;

  notation)
    require_tools notation
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
    ;;

  none)
    if [[ -z "$target_registry" ]]; then
      warn "verify_mode=none: skipping image signing. Image will be REJECTED by Kyverno-enforcing clusters."
    fi
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
print_kv "Model"                     "${model_name}@v${model_version} (${model_source})"
print_kv "Image (digest)"            "$image_digest_ref"
print_kv "Image (tag)"               "$image_tag_ref"
print_kv "Base Image"                "$inference_base_image"
print_kv "Verify Mode"               "$verify_mode"
print_kv "Build Context (transient)" "$build_ctx"
info "Done."
if [[ -z "$target_registry" ]]; then
  info "Next: attach SBOM/VEX attestations via attest-image.sh:"
  info "  fleet-deployment/setup/attest-image.sh --image '$image_digest_ref'"
fi
