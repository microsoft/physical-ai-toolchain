#!/usr/bin/env bash
# Start the host-local registry and its pull-through caches, then register them
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./registry-common.sh
source "$SCRIPT_DIR/registry-common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Start the host-local image registry and one pull-through cache per upstream
declared in upstreams.conf, then point k3s and podman at them so no image is
fetched twice over a slow link.

Two kinds of registry are started:

  hosting   Read-write, holds images built in this repository. Push here.
  cache     Read-only mirror of one upstream, populated on first pull.

Every endpoint binds 127.0.0.1. k3s runs on the host, so its containerd reaches
the same loopback address the build tooling uses.

Credentials are read from the environment named in upstreams.conf and are never
written to the repository. Anonymous access is enough for public images; nvcr.io
and Azure Container Registry need credentials for anything else.

Writing the k3s mirror configuration requires sudo and restarts k3s. Everything
else runs unprivileged.

OPTIONS:
    -h, --help          Show this help message
    --config-preview    Print configuration and exit
    --skip-k3s          Start the registries but leave k3s untouched
    --print-k3s-config  Print the k3s registries.yaml and exit

ENVIRONMENT:
    GPU_OFFLOAD_REGISTRY_HOST   Hosting endpoint (default: localhost:5000)
    GPU_OFFLOAD_REGISTRY_DATA   Blob storage root on the host
    GPU_OFFLOAD_REGISTRY_IMAGE  Registry image reference
    GPU_OFFLOAD_ACR_NAME        Azure Container Registry name, without suffix

    Per-upstream credentials use the variables named in upstreams.conf.

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --config-preview
    GPU_OFFLOAD_ACR_NAME=myregistry $(basename "$0")
EOF
}

# Defaults
config_preview=false
skip_k3s=false
print_k3s_config=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    --config-preview)    config_preview=true; shift ;;
    --skip-k3s)          skip_k3s=true; shift ;;
    --print-k3s-config)  print_k3s_config=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

require_tools podman curl envsubst

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

eval "$("$SCRIPT_DIR/../scripts/detect-platform.sh" --export)"
registry_load_upstreams

if [[ "$print_k3s_config" == "true" ]]; then
  registry_render_k3s_config
  exit 0
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Hosting endpoint" "$REGISTRY_HOST"
  print_kv "Blob storage root" "$REGISTRY_DATA"
  print_kv "Registry image" "$REGISTRY_IMAGE"
  print_kv "Upstream table" "$REGISTRY_UPSTREAMS_FILE"
  print_kv "Cluster runtime" "$GPU_OFFLOAD_RUNTIME"
  print_kv "k3s registries file" "$REGISTRY_K3S_FILE"
  print_kv "podman registries file" "$REGISTRY_PODMAN_FILE"
  for spec in "${REGISTRY_UPSTREAMS[@]}"; do
    IFS='|' read -r host port remote username _ <<< "$spec"
    if [[ -n "$username" ]]; then
      print_kv "Cache $host" "localhost:$port -> $remote (authenticated)"
    else
      print_kv "Cache $host" "localhost:$port -> $remote (anonymous)"
    fi
  done
  exit 0
fi

#------------------------------------------------------------------------------
# Hosting Registry
#------------------------------------------------------------------------------
section "Hosting Registry"

start_registry() {
  local name="$1" port="$2" data="$3"
  shift 3

  if podman container exists "$name"; then
    if registry_is_running "$name"; then
      info "Container $name already running"
    else
      podman start "$name" > /dev/null
      info "Started existing container $name"
    fi
    return 0
  fi

  mkdir -p "$data"
  podman run --detach \
    --name "$name" \
    --restart always \
    --publish "127.0.0.1:$port:5000" \
    --volume "$data:/var/lib/registry:z" \
    "$@" \
    "$REGISTRY_IMAGE" > /dev/null
  info "Created container $name"
}

start_registry "$REGISTRY_HOSTING_CONTAINER" "$REGISTRY_PORT" "$REGISTRY_DATA/hosting"
registry_wait_until_answering "$REGISTRY_HOST" \
  || fatal "Hosting registry did not answer on http://$REGISTRY_HOST/v2/"
info "Hosting registry answering on http://$REGISTRY_HOST/v2/"

#------------------------------------------------------------------------------
# Pull-through Caches
#------------------------------------------------------------------------------
section "Pull-through Caches"

if [[ ${#REGISTRY_UPSTREAMS[@]} -eq 0 ]]; then
  info "No upstreams configured"
fi

for spec in "${REGISTRY_UPSTREAMS[@]}"; do
  IFS='|' read -r host port remote username password <<< "$spec"
  container="$(registry_container_for_port "$port")"

  env_args=(--env "REGISTRY_PROXY_REMOTEURL=$remote")
  if [[ -n "$username" ]]; then
    env_args+=(--env "REGISTRY_PROXY_USERNAME=$username" --env "REGISTRY_PROXY_PASSWORD=$password")
  elif [[ "$host" == "nvcr.io" || "$host" == *.azurecr.io ]]; then
    warn "$host cache is anonymous; private images will fail to pull"
  fi

  start_registry "$container" "$port" "$REGISTRY_DATA/cache-$port" "${env_args[@]}"
  registry_wait_until_answering "localhost:$port" \
    || fatal "Cache for $host did not answer on http://localhost:$port/v2/"
  info "Caching $host at http://localhost:$port"
done

#------------------------------------------------------------------------------
# podman Registration
#------------------------------------------------------------------------------
section "podman Registration"

mkdir -p "$(dirname "$REGISTRY_PODMAN_FILE")"
if [[ -f "$REGISTRY_PODMAN_FILE" ]] \
  && [[ "$(cat "$REGISTRY_PODMAN_FILE")" == "$(registry_render_podman_config)" ]]; then
  info "podman already routes through the caches"
else
  registry_render_podman_config > "$REGISTRY_PODMAN_FILE"
  info "Wrote $REGISTRY_PODMAN_FILE"
fi

#------------------------------------------------------------------------------
# k3s Mirror Registration
#------------------------------------------------------------------------------
section "k3s Mirror Registration"

if [[ "$skip_k3s" == "true" ]]; then
  info "Skipped on request"
elif [[ "$GPU_OFFLOAD_RUNTIME" != "k3s" ]]; then
  warn "Cluster runtime is $GPU_OFFLOAD_RUNTIME; skipping the k3s mirror"
  warn "A kind node runs in a container and cannot reach 127.0.0.1 on the host"
else
  # containerd rejects a plain-HTTP registry unless the endpoint is declared,
  # and it reads registries.yaml only when k3s starts.
  desired_config="$(registry_render_k3s_config)"

  if sudo test -f "$REGISTRY_K3S_FILE" \
    && [[ "$(sudo cat "$REGISTRY_K3S_FILE")" == "$desired_config" ]]; then
    info "k3s mirror configuration is current"
  else
    sudo mkdir -p "$(dirname "$REGISTRY_K3S_FILE")"
    printf '%s\n' "$desired_config" | sudo tee "$REGISTRY_K3S_FILE" > /dev/null
    info "Wrote $REGISTRY_K3S_FILE"
    sudo systemctl restart k3s
    require_tools kubectl
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait --for=condition=Ready node --all --timeout=180s > /dev/null
    info "Restarted k3s and reloaded the mirror configuration"
  fi
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Hosting endpoint" "$REGISTRY_HOST"
print_kv "Blob storage root" "$REGISTRY_DATA"
print_kv "Caches started" "${#REGISTRY_UPSTREAMS[@]}"
print_kv "k3s registries file" "$REGISTRY_K3S_FILE"
print_kv "podman registries file" "$REGISTRY_PODMAN_FILE"
info "Push with: registry/registry-push.sh <repository>:<tag>"
