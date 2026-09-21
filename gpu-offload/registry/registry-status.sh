#!/usr/bin/env bash
# Report the state of the host-local registry, its caches, and their registration
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./registry-common.sh
source "$SCRIPT_DIR/registry-common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Report whether each registry container is running and answering, how much disk
its blobs occupy, and whether k3s and podman are registered against them. Also
list the repositories held by the hosting registry.

OPTIONS:
    -h, --help          Show this help message
    --config-preview    Print configuration and exit

ENVIRONMENT:
    GPU_OFFLOAD_REGISTRY_HOST   Hosting endpoint (default: localhost:5000)
    GPU_OFFLOAD_ACR_NAME        Azure Container Registry name, without suffix

EXAMPLES:
    $(basename "$0")
EOF
}

# Defaults
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools podman curl envsubst

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

registry_load_upstreams

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Hosting endpoint" "$REGISTRY_HOST"
  print_kv "Upstream table" "$REGISTRY_UPSTREAMS_FILE"
  print_kv "Upstreams" "${#REGISTRY_UPSTREAMS[@]}"
  exit 0
fi

disk_usage_of() {
  local path="$1"
  if [[ -d "$path" ]]; then
    du --summarize --human-readable "$path" 2> /dev/null | cut -f1
  else
    printf 'empty'
  fi
}

describe_endpoint() {
  local container="$1" endpoint="$2" data="$3"
  if ! podman container exists "$container"; then
    printf 'absent'
  elif ! registry_is_running "$container"; then
    printf 'stopped'
  elif curl --silent --output /dev/null --max-time 2 "http://$endpoint/v2/"; then
    printf 'running, %s cached' "$(disk_usage_of "$data")"
  else
    printf 'running but not answering'
  fi
}

#------------------------------------------------------------------------------
# Registries
#------------------------------------------------------------------------------
section "Registries"

print_kv "$REGISTRY_HOST (hosting)" \
  "$(describe_endpoint "$REGISTRY_HOSTING_CONTAINER" "$REGISTRY_HOST" "$REGISTRY_DATA/hosting")"

for spec in "${REGISTRY_UPSTREAMS[@]}"; do
  IFS='|' read -r host port _ _ _ <<< "$spec"
  container="$(registry_container_for_port "$port")"
  print_kv "$host" \
    "$(describe_endpoint "$container" "localhost:$port" "$REGISTRY_DATA/cache-$port")"
done

#------------------------------------------------------------------------------
# Hosted Repositories
#------------------------------------------------------------------------------
section "Hosted Repositories"

if catalog="$(curl --silent --fail --max-time 5 "http://$REGISTRY_HOST/v2/_catalog" 2> /dev/null)"; then
  repositories="$(printf '%s' "$catalog" | sed 's/.*\[//; s/\].*//; s/"//g; s/,/ /g')"
  if [[ -z "${repositories// /}" ]]; then
    info "No images pushed yet"
  else
    for repository in $repositories; do
      tags="$(curl --silent --fail --max-time 5 "http://$REGISTRY_HOST/v2/$repository/tags/list" 2> /dev/null \
        | sed 's/.*\[//; s/\].*//; s/"//g; s/,/ /g')"
      print_kv "$repository" "${tags:-no tags}"
    done
  fi
else
  warn "Hosting registry is not answering"
fi

#------------------------------------------------------------------------------
# Runtime Registration
#------------------------------------------------------------------------------
section "Runtime Registration"

if [[ -f "$REGISTRY_PODMAN_FILE" ]]; then
  if [[ "$(cat "$REGISTRY_PODMAN_FILE")" == "$(registry_render_podman_config)" ]]; then
    print_kv "podman" "current ($REGISTRY_PODMAN_FILE)"
  else
    print_kv "podman" "stale, re-run registry-up.sh"
  fi
else
  print_kv "podman" "not registered, run registry-up.sh"
fi

# Reading the k3s file needs root, so report only what is visible unprivileged.
if sudo --non-interactive test -f "$REGISTRY_K3S_FILE" 2> /dev/null; then
  if [[ "$(sudo --non-interactive cat "$REGISTRY_K3S_FILE" 2> /dev/null)" == "$(registry_render_k3s_config)" ]]; then
    print_kv "k3s" "current ($REGISTRY_K3S_FILE)"
  else
    print_kv "k3s" "stale, re-run registry-up.sh"
  fi
else
  print_kv "k3s" "unreadable without sudo; run registry-up.sh to write it"
fi
