#!/usr/bin/env bash
# Import the pinned OSMO images and Helm charts into the environment's ACR and write osmo-images.json.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

OSMO_SOURCE_REGISTRY="nvcr.io/nvidia/osmo"
OSMO_IMAGE_COMPONENTS=(
  agent backend-listener backend-worker client delayed-job-monitor init-container
  logger router service web-ui worker
)

show_help() {
  cat << EOF
Usage: $(basename "$0") (--environment NAME | --bundle-dir DIR) [OPTIONS]

Import the pinned OSMO images from $OSMO_SOURCE_REGISTRY and the OSMO service and
backend-operator charts from the NGC Helm repository into the environment's ACR.
Every imported tag is locked against writes and deletes. The script then writes
osmo-images.json to the bundle directory. When the bundle has deployment.json, it
records the manifest, registry, and OSMO image and chart versions there. A field
that's already set must match this import, or the script stops before importing.

Tags with writes disabled are reused; chart tags are checked against the pinned SHA-256 first.
Unlocked tags are refreshed from the source. Use the manifest with
03-deploy-osmo.sh --use-acr --image-manifest and with 04-prepare-osmo-hil-node.sh.

OPTIONS:
    -h, --help               Show this help message
    -e, --environment NAME   Environment bundle name (bundle: generated/<environment>)
    --bundle-dir DIR         Bundle directory for osmo-images.json
    -t, --tf-dir DIR         Terraform directory (default: $DEFAULT_TF_DIR)
    --acr-name NAME          Container registry (default: Terraform output)
    --image-version TAG      OSMO image tag (default: $OSMO_IMAGE_VERSION)
    --chart-version VER      OSMO chart version (default: $OSMO_CHART_VERSION)
    --config-preview         Print configuration and exit

The chart SHA-256 pins come from OSMO_SERVICE_CHART_SHA256 and
OSMO_BACKEND_CHART_SHA256 in defaults.conf; set both when you change the chart version.

EXAMPLES:
    $(basename "$0") --environment dev-001 --config-preview
    $(basename "$0") --environment dev-001
    $(basename "$0") --bundle-dir /path/to/bundle --acr-name myregistry
EOF
}

# Defaults
environment=""
bundle_dir=""
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
acr_name=""
image_version="$OSMO_IMAGE_VERSION"
chart_version="$OSMO_CHART_VERSION"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    -e|--environment)   environment="$2"; shift 2 ;;
    --bundle-dir)       bundle_dir="$2"; shift 2 ;;
    -t|--tf-dir)        tf_dir="$2"; shift 2 ;;
    --acr-name)         acr_name="$2"; shift 2 ;;
    --image-version)    image_version="$2"; shift 2 ;;
    --chart-version)    chart_version="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

if [[ -n "$environment" ]]; then
  [[ "$environment" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || \
    fatal "Environment must use lowercase letters, numbers, and internal hyphens"
  bundle_dir="${bundle_dir:-$SCRIPT_DIR/generated/$environment}"
fi
[[ -n "$bundle_dir" ]] || fatal "--environment or --bundle-dir is required"
[[ ! -L "$bundle_dir" ]] || fatal "Bundle directory must not be a symlink: $bundle_dir"
[[ -n "$image_version" && -n "$chart_version" ]] || fatal "Image and chart versions must not be empty"
[[ -n "$OSMO_SERVICE_CHART_SHA256" && -n "$OSMO_BACKEND_CHART_SHA256" ]] || \
  fatal "OSMO_SERVICE_CHART_SHA256 and OSMO_BACKEND_CHART_SHA256 must be set"

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

if [[ -z "$acr_name" ]]; then
  require_tools terraform jq
  tf_output=$(read_terraform_outputs "$tf_dir")
  acr_name=$(tf_get "$tf_output" "container_registry.value.name")
  [[ -n "$acr_name" ]] || fatal "No container_registry Terraform output; pass --acr-name"
fi
[[ "$acr_name" =~ ^[a-zA-Z0-9]{5,50}$ ]] || fatal "Invalid container registry name: $acr_name"
acr_name=$(tr '[:upper:]' '[:lower:]' <<< "$acr_name")
manifest_file="$bundle_dir/osmo-images.json"
deployment_file="$bundle_dir/deployment.json"
chart_entries=(
  "$OSMO_SERVICE_CHART|$OSMO_SERVICE_CHART_SHA256"
  "$OSMO_BACKEND_CHART|$OSMO_BACKEND_CHART_SHA256"
)

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Registry" "$acr_name"
  print_kv "Image Source" "$OSMO_SOURCE_REGISTRY/<component>:$image_version"
  print_kv "Image Target" "osmo/<component>:$image_version"
  print_kv "Components" "${OSMO_IMAGE_COMPONENTS[*]}"
  print_kv "Chart Source" "$HELM_REPO_OSMO"
  print_kv "Chart Target" "helm/$OSMO_SERVICE_CHART, helm/$OSMO_BACKEND_CHART ($chart_version)"
  print_kv "Service Chart SHA" "$OSMO_SERVICE_CHART_SHA256"
  print_kv "Backend Chart SHA" "$OSMO_BACKEND_CHART_SHA256"
  print_kv "Tag Policy" "reuse tags with writes disabled, refresh writable tags, lock every tag"
  print_kv "Image Manifest" "$manifest_file"
  print_kv "Deployment Metadata" "$([[ -f $deployment_file ]] && echo "update $deployment_file" || echo 'not present; nothing to update')"
  exit 0
fi

require_tools az jq helm
az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
login_server=$(az acr show --name "$acr_name" --query loginServer -o tsv)
[[ -n "$login_server" ]] || fatal "Unable to read the login server for $acr_name"

# deployment.json fields that must describe this import. Each one may be empty, which the
# import fills in, or already set to the same value.
deployment_pins=$(jq -n --arg acr "$acr_name" --arg login "$login_server" \
  --arg image "$image_version" --arg chart "$chart_version" '
  {acr_name: $acr, acr_login_server: $login, osmo_image_version: $image, osmo_chart_version: $chart}
')
# shellcheck disable=SC2016  # jq filter
deployment_conflicts_jq='def conflicts($pins): . as $doc | [$pins | to_entries[]
  | select(($doc[.key] // "") as $value | $value != "" and $value != .value)
  | "\(.key) is \($doc[.key]), not \(.value)"];'

if [[ -f "$deployment_file" ]]; then
  [[ ! -L "$deployment_file" ]] || fatal "deployment.json must not be a symlink: $deployment_file"
  conflicts=$(jq -r --argjson pins "$deployment_pins" "$deployment_conflicts_jq"' conflicts($pins) | join("; ")' \
    "$deployment_file") || fatal "Unable to read $deployment_file"
  [[ -z "$conflicts" ]] || fatal "deployment.json doesn't match this import: $conflicts"
fi

work_dir=$(mktemp -d)
manifest_tmp=""
deployment_tmp=""
trap 'rm -rf "$work_dir" "$manifest_tmp" "$deployment_tmp"' EXIT
export HELM_REPOSITORY_CONFIG="$work_dir/helm/repositories.yaml"
export HELM_REPOSITORY_CACHE="$work_dir/helm/cache"
export HELM_REGISTRY_CONFIG="$work_dir/helm/registry.json"

acr_repositories=$(az acr repository list --name "$acr_name" -o tsv)

acr_tag_exists() {
  local repository="$1" tag="$2"
  grep -Fqx -- "$repository" <<< "$acr_repositories" || return 1
  [[ "$(az acr repository show-tags --name "$acr_name" --repository "$repository" \
    --query "contains(@, '$tag')" -o tsv)" == "true" ]]
}

acr_tag_write_locked() {
  local image="$1"
  az acr repository show --name "$acr_name" --image "$image" \
    --query changeableAttributes.writeEnabled -o json | jq -e '. == false' >/dev/null
}

lock_acr_tag() {
  local image="$1"
  az acr repository update --name "$acr_name" --image "$image" \
    --write-enabled false --delete-enabled false --output none
}

#------------------------------------------------------------------------------
# Import Images
#------------------------------------------------------------------------------
section "Import OSMO Images"

images_imported=0
images_reused=0
for component in "${OSMO_IMAGE_COMPONENTS[@]}"; do
  repository="osmo/$component"
  source_image="$OSMO_SOURCE_REGISTRY/$component:$image_version"
  import_args=(--name "$acr_name" --source "$source_image" --image "$repository:$image_version" --output none)
  if acr_tag_exists "$repository" "$image_version"; then
    # A tag with writes disabled can't have changed since it was locked; finish the lock and reuse it.
    if acr_tag_write_locked "$repository:$image_version"; then
      lock_acr_tag "$repository:$image_version"
      info "Reusing locked $repository:$image_version"
      images_reused=$((images_reused + 1))
      continue
    fi
    import_args+=(--force)
  fi
  info "Importing $source_image..."
  az acr import "${import_args[@]}" || fatal "Import failed for $source_image"
  lock_acr_tag "$repository:$image_version"
  images_imported=$((images_imported + 1))
done

#------------------------------------------------------------------------------
# Import Charts
#------------------------------------------------------------------------------
section "Import OSMO Charts"

helm repo add osmo "$HELM_REPO_OSMO" >/dev/null || fatal "Unable to add the OSMO Helm repository $HELM_REPO_OSMO"
helm repo update osmo >/dev/null || fatal "Unable to update the OSMO Helm repository $HELM_REPO_OSMO"
az acr login --name "$acr_name" --expose-token --query accessToken -o tsv --only-show-errors | \
  helm registry login "$login_server" --username 00000000-0000-0000-0000-000000000000 --password-stdin >/dev/null || \
  fatal "Unable to log Helm in to $login_server"

charts_imported=0
charts_reused=0
for entry in "${chart_entries[@]}"; do
  IFS='|' read -r chart expected_sha <<< "$entry"
  repository="helm/$chart"
  if acr_tag_exists "$repository" "$chart_version" && acr_tag_write_locked "$repository:$chart_version"; then
    helm pull "oci://$login_server/$repository" --version "$chart_version" \
      --destination "$work_dir/acr-$chart" >/dev/null || fatal "Unable to pull $repository:$chart_version from $acr_name"
    actual_sha=$(calculate_sha256 "$(find_latest_chart_archive "$work_dir/acr-$chart")")
    [[ "$actual_sha" == "$expected_sha" ]] || \
      fatal "Locked $repository:$chart_version in $acr_name doesn't match the pinned SHA-256 ($actual_sha); investigate before unlocking it"
    lock_acr_tag "$repository:$chart_version"
    info "Reusing locked $repository:$chart_version (SHA-256 verified)"
    charts_reused=$((charts_reused + 1))
    continue
  fi
  chart_archive=$(pull_and_verify_chart "osmo/$chart" "$chart_version" "$expected_sha" "$work_dir/source-$chart")
  info "Pushing $chart $chart_version to oci://$login_server/helm..."
  helm push "$chart_archive" "oci://$login_server/helm" >/dev/null || fatal "Push failed for $chart $chart_version"
  lock_acr_tag "$repository:$chart_version"
  charts_imported=$((charts_imported + 1))
done

#------------------------------------------------------------------------------
# Write Image Manifest
#------------------------------------------------------------------------------
section "Write Image Manifest"

mkdir -p "$bundle_dir"
images_json='{}'
for component in "${OSMO_IMAGE_COMPONENTS[@]}"; do
  digest=$(az acr manifest show-metadata --registry "$acr_name" \
    --name "osmo/$component:$image_version" --query digest -o tsv)
  images_json=$(jq --arg component "$component" --arg repository "osmo/$component" --arg digest "$digest" \
    '. + {($component): {repository: $repository, digest: $digest}}' <<< "$images_json")
done
manifest_tmp=$(mktemp "$bundle_dir/.osmo-images.XXXXXX")
jq -n --arg registry "$acr_name" --arg login_server "$login_server" --arg version "$image_version" \
  --argjson images "$images_json" '
  {schema_version: 1, registry: $registry, login_server: $login_server, image_version: $version, images: $images}
' > "$manifest_tmp"
chmod 0644 "$manifest_tmp"
mv "$manifest_tmp" "$manifest_file"
verify_acr_image_manifest "$manifest_file" "$login_server" "$image_version"

deployment_status="not present"
if [[ -f "$deployment_file" ]]; then
  manifest_sha=$(calculate_sha256 "$manifest_file")
  deployment_tmp=$(mktemp "$bundle_dir/.deployment.XXXXXX")
  # Check the pinned fields again in the same pass that writes them, then swap the file in one rename.
  jq --argjson pins "$deployment_pins" --arg sha "$manifest_sha" \
    --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$deployment_conflicts_jq"'
    conflicts($pins) as $conflicts |
    if ($conflicts | length) > 0 then error("deployment.json changed during the import: " + ($conflicts | join("; ")))
    else . + $pins | .artifacts.osmo_images = {file: "osmo-images.json", sha256: $sha} | .generated_at = $now
    end
  ' "$deployment_file" > "$deployment_tmp" || fatal "Unable to update $deployment_file"
  chmod 0644 "$deployment_tmp"
  mv "$deployment_tmp" "$deployment_file"
  deployment_status="manifest, registry, and versions recorded"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Registry" "$login_server"
print_kv "Images" "$images_imported imported, $images_reused reused (locked)"
print_kv "Charts" "$charts_imported imported, $charts_reused reused (locked)"
print_kv "Image Version" "$image_version"
print_kv "Chart Version" "$chart_version"
print_kv "Image Manifest" "$manifest_file"
print_kv "Deployment Metadata" "$deployment_status"
info "Deploy with 03-deploy-osmo.sh --use-acr --image-manifest $manifest_file"
info "HiL hosts can use --backend-chart-ref oci://$login_server/helm/$OSMO_BACKEND_CHART with 04-prepare-osmo-hil-node.sh"
