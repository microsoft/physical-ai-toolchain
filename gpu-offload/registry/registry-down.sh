#!/usr/bin/env bash
# Stop the host-local registry and its pull-through caches
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./registry-common.sh
source "$SCRIPT_DIR/registry-common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Stop the hosting registry and every pull-through cache. Cached blobs are kept on
the host, so a later registry-up.sh serves the same images without refetching
them.

Pass --purge to delete the blobs as well. That discards every cached upstream
image and every image built here, and the next deployment refetches all of them
over the internet.

The k3s and podman registration files are left in place: they describe endpoints
rather than content, and re-running registry-up.sh restores service without
another k3s restart.

OPTIONS:
    -h, --help          Show this help message
    --config-preview    Print configuration and exit
    --purge             Remove containers and delete all cached blobs

ENVIRONMENT:
    GPU_OFFLOAD_REGISTRY_HOST   Hosting endpoint (default: localhost:5000)
    GPU_OFFLOAD_REGISTRY_DATA   Blob storage root on the host
    GPU_OFFLOAD_ACR_NAME        Azure Container Registry name, without suffix

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --purge
EOF
}

# Defaults
config_preview=false
purge=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --config-preview)  config_preview=true; shift ;;
    --purge)           purge=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools podman envsubst

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

registry_load_upstreams

containers=("$REGISTRY_HOSTING_CONTAINER")
data_paths=("$REGISTRY_DATA/hosting")
for spec in "${REGISTRY_UPSTREAMS[@]}"; do
  IFS='|' read -r _ port _ _ _ <<< "$spec"
  containers+=("$(registry_container_for_port "$port")")
  data_paths+=("$REGISTRY_DATA/cache-$port")
done

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Containers" "${containers[*]}"
  print_kv "Blob storage root" "$REGISTRY_DATA"
  print_kv "Purge blobs" "$purge"
  exit 0
fi

#------------------------------------------------------------------------------
# Stop Registries
#------------------------------------------------------------------------------
section "Stop Registries"

for container in "${containers[@]}"; do
  if ! podman container exists "$container"; then
    info "Container $container is absent"
    continue
  fi

  if [[ "$purge" == "true" ]]; then
    # --restart always brings a merely stopped container back, so purging has
    # to remove it outright.
    podman rm --force "$container" > /dev/null
    info "Removed container $container"
  else
    podman stop "$container" > /dev/null
    info "Stopped container $container"
  fi
done

#------------------------------------------------------------------------------
# Blob Storage
#------------------------------------------------------------------------------
section "Blob Storage"

if [[ "$purge" == "true" ]]; then
  for data_path in "${data_paths[@]}"; do
    if [[ -d "$data_path" ]]; then
      rm --recursive --force "$data_path"
      info "Deleted $data_path"
    fi
  done
else
  info "Kept cached blobs under $REGISTRY_DATA"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Containers handled" "${#containers[@]}"
print_kv "Blob storage root" "$REGISTRY_DATA"
print_kv "Blobs purged" "$purge"
