#!/usr/bin/env bash
# Log the OSMO CLI in to the local OSMO service, default it to the local pool, and give it a DATA credential
# for the local object store.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../data-pipeline/setup/defaults.conf
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"
# shellcheck source=../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"
# shellcheck source=../../scripts/lib/local-cluster.sh
source "$REPO_ROOT/scripts/lib/local-cluster.sh"

CREDENTIAL_NAME="local-object-store"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Log the OSMO CLI in to the local OSMO service with a dev login, make $LOCAL_OSMO_POOL its
default pool, and store a DATA credential for s3://$LOCAL_OSMO_BUCKET so 'osmo data
upload' and 'osmo data download' reach the local object store. Credential values are
read from Kubernetes and passed through file descriptors, never through arguments.
The OSMO CLI keeps the credential in its config directory with mode 0600.

The OSMO CLI holds one login per config directory ($LOCAL_OSMO_CONFIG_DIR).
A login to another OSMO service is kept unless --force is given; set
LOCAL_OSMO_CONFIG_DIR (or OSMO_CONFIG_FILE_DIR) to keep both.

OPTIONS:
    -h, --help               Show this help message
    --force                  Replace a login to another OSMO service
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    LOCAL_OSMO_CONFIG_DIR=\$HOME/.config/osmo-local $(basename "$0")
EOF
}

force=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --force)            force=true; shift ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

require_tools osmo kubectl jq curl base64

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local
service_url="$PROFILE_OSMO_URL"
export OSMO_CONFIG_FILE_DIR="$PROFILE_OSMO_CONFIG_DIR"
current_url="$(osmo_login_url "$PROFILE_OSMO_CONFIG_DIR")"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Service URL" "$service_url"
  print_kv "Config Directory" "$LOCAL_OSMO_CONFIG_DIR"
  print_kv "Current Login" "${current_url:-none}"
  print_kv "User" "$LOCAL_OSMO_USER (dev login)"
  print_kv "Pool" "$LOCAL_OSMO_POOL"
  print_kv "DATA Credential" "$CREDENTIAL_NAME for s3://$LOCAL_OSMO_BUCKET via $PROFILE_S3_ENDPOINT"
  exit 0
fi

if [[ -n "$current_url" && "${current_url%/}" != "${service_url%/}" && "$force" != "true" ]]; then
  fatal "The OSMO CLI in $LOCAL_OSMO_CONFIG_DIR is logged in to $current_url; rerun with --force to replace it, or set LOCAL_OSMO_CONFIG_DIR to another directory"
fi

local_cluster_activate

#------------------------------------------------------------------------------
# Login and Pool
#------------------------------------------------------------------------------
section "Login and Pool"

osmo_login "$service_url" "$LOCAL_OSMO_USER" >/dev/null
osmo profile set pool "$LOCAL_OSMO_POOL" >/dev/null
info "Logged in to $service_url as $LOCAL_OSMO_USER with default pool $LOCAL_OSMO_POOL"

#------------------------------------------------------------------------------
# DATA Credential
#------------------------------------------------------------------------------
section "DATA Credential"

s3_secret_value() {
  kubectl -n "$LOCAL_PLATFORM_NAMESPACE" get secret "$LOCAL_S3_SECRET" -o json \
    | jq -r --arg key "$1" '.data[$key] // error("missing key \($key)")' | base64 -d
}

# Setting the same name again replaces the credential, so reruns never duplicate it.
osmo credential set "$CREDENTIAL_NAME" --type DATA --payload-file \
  access_key_id=<(s3_secret_value access-key-id) \
  access_key=<(s3_secret_value secret-access-key) \
  endpoint=<(printf 's3://%s' "$LOCAL_OSMO_BUCKET") \
  region=<(printf '%s' "$LOCAL_S3_REGION") \
  override_url=<(printf '%s' "$PROFILE_S3_ENDPOINT") \
  addressing_style=<(printf 'path') >/dev/null
info "Stored DATA credential $CREDENTIAL_NAME for s3://$LOCAL_OSMO_BUCKET"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Service URL" "$service_url"
print_kv "Config Directory" "$LOCAL_OSMO_CONFIG_DIR"
print_kv "Pool" "$LOCAL_OSMO_POOL"
print_kv "DATA Credential" "$CREDENTIAL_NAME (s3://$LOCAL_OSMO_BUCKET)"
info "The OSMO CLI is connected to the local OSMO service"
