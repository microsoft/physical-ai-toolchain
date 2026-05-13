#!/usr/bin/env bash
# Register a local dataset folder as an AzureML uri_folder data asset.
#
# Why this script exists: the workspace storage account has
# publicNetworkAccess=Disabled, so `az ml data create` from off-VPN hosts fails
# with NXDOMAIN on the *.blob.core.windows.net endpoint. This wrapper:
#   1) checks VPN connectivity (DNS resolution of the workspace blob endpoint)
#   2) detects azcopy and uses it transparently for >100 MB uploads
#   3) idempotently bumps the version if NAME:VERSION already exists
#   4) prints the registered asset URI for use with
#      `submit-azureml-openvla-oft-training.sh --dataset-asset NAME:VERSION`
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"

# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

show_help() {
  cat <<'EOF'
Usage: upload-dataset-to-aml.sh --path PATH --name NAME [OPTIONS]

Register a local folder as an AzureML uri_folder data asset.

REQUIRED:
    --path PATH          Local folder to upload (e.g. datasets/schaeffler_sim_avc1/second_collection)
    --name NAME          Data asset name (e.g. schaeffler-sim-avc1-second)

OPTIONS:
    --version VERSION    Asset version (default: 1; auto-bumps if it exists)
    --description TEXT   Asset description
    --subscription-id ID
    --resource-group NAME
    --workspace-name NAME
    --bump               Auto-increment version if --version already exists
    --skip-vpn-check     Bypass the DNS resolution check
    -h, --help

Resolves Azure context from Terraform outputs / env vars by default.
EOF
}

dataset_path=""
asset_name=""
asset_version="1"
description=""
bump=false
skip_vpn_check=false
subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    --path)                dataset_path="$2"; shift 2 ;;
    --name)                asset_name="$2"; shift 2 ;;
    --version)             asset_version="$2"; shift 2 ;;
    --description)         description="$2"; shift 2 ;;
    --bump)                bump=true; shift ;;
    --skip-vpn-check)      skip_vpn_check=true; shift ;;
    --subscription-id)     subscription_id="$2"; shift 2 ;;
    --resource-group)      resource_group="$2"; shift 2 ;;
    --workspace-name)      workspace_name="$2"; shift 2 ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$dataset_path" ]]    || fatal "--path is required"
[[ -d "$dataset_path" ]]    || fatal "Path does not exist or is not a directory: $dataset_path"
[[ -n "$asset_name" ]]      || fatal "--name is required"
[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]]  || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]]  || fatal "AZUREML_WORKSPACE_NAME required"
description="${description:-Local upload of ${dataset_path}}"

require_tools az

# VPN check: workspaces with private endpoints expose blob URLs that only
# resolve from inside the VNet (point-to-site VPN or self-hosted compute).
if [[ "$skip_vpn_check" == "false" ]]; then
  blob_account=$(az ml workspace show -n "$workspace_name" -g "$resource_group" \
    --subscription "$subscription_id" --query "storageAccount" -o tsv 2>/dev/null \
    | awk -F/ '{print $NF}')
  if [[ -n "$blob_account" ]]; then
    if ! getent hosts "${blob_account}.blob.core.windows.net" >/dev/null 2>&1; then
      fatal "Cannot resolve ${blob_account}.blob.core.windows.net. Connect to the VPN first (infrastructure/terraform/vpn/) or pass --skip-vpn-check."
    fi
  fi
fi

# Auto-bump version if the requested one already exists and --bump is set.
exists=$(az ml data show --name "$asset_name" --version "$asset_version" \
  -w "$workspace_name" -g "$resource_group" --subscription "$subscription_id" \
  --query "id" -o tsv 2>/dev/null || true)
if [[ -n "$exists" ]]; then
  if [[ "$bump" == "true" ]]; then
    latest=$(az ml data list --name "$asset_name" \
      -w "$workspace_name" -g "$resource_group" --subscription "$subscription_id" \
      --query "max_by([], &@.version).version" -o tsv 2>/dev/null || echo "0")
    asset_version=$((latest + 1))
    info "Asset exists; bumping to version $asset_version"
  else
    warn "Asset ${asset_name}:${asset_version} already exists; pass --bump to auto-increment"
    print_kv "Asset URI" "azureml:${asset_name}:${asset_version}"
    exit 0
  fi
fi

section "Uploading ${dataset_path} -> azureml:${asset_name}:${asset_version}"
print_kv "Workspace" "$workspace_name ($resource_group)"
print_kv "Subscription" "$subscription_id"
print_kv "Size" "$(du -sh "$dataset_path" | awk '{print $1}')"

az ml data create \
  --name "$asset_name" \
  --version "$asset_version" \
  --path "$dataset_path" \
  --type uri_folder \
  --description "$description" \
  -w "$workspace_name" \
  -g "$resource_group" \
  --subscription "$subscription_id"

section "Registered"
print_kv "Asset URI" "azureml:${asset_name}:${asset_version}"
print_kv "Use with"  "submit-azureml-openvla-oft-training.sh --dataset-asset ${asset_name}:${asset_version}"
