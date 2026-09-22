#!/usr/bin/env bash
# Shared configuration and helpers for the host-local registry scripts
#
# Sourced by registry-up.sh, registry-down.sh, registry-status.sh, and
# registry-push.sh. Defines the endpoint layout and parses upstreams.conf.
#
# shellcheck disable=SC2034  # configuration is consumed by the sourcing scripts

REGISTRY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_REPO_ROOT="$(git -C "$REGISTRY_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$REGISTRY_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REGISTRY_REPO_ROOT/scripts/lib/common.sh"

REGISTRY_HOST="${GPU_OFFLOAD_REGISTRY_HOST:-localhost:5000}"
REGISTRY_PORT="${REGISTRY_HOST##*:}"
REGISTRY_DATA="${GPU_OFFLOAD_REGISTRY_DATA:-$HOME/.local/share/gpu-offload/registry}"
REGISTRY_IMAGE="${GPU_OFFLOAD_REGISTRY_IMAGE:-docker.io/library/registry:2}"
REGISTRY_UPSTREAMS_FILE="${GPU_OFFLOAD_UPSTREAMS_FILE:-$REGISTRY_DIR/upstreams.conf}"
REGISTRY_HOSTING_CONTAINER="gpu-offload-registry"
REGISTRY_K3S_FILE="/etc/rancher/k3s/registries.yaml"
REGISTRY_PODMAN_FILE="$HOME/.config/containers/registries.conf.d/gpu-offload.conf"

if [[ "$REGISTRY_PORT" == "$REGISTRY_HOST" ]]; then
  fatal "GPU_OFFLOAD_REGISTRY_HOST must include a port, for example localhost:5000"
fi

# Populated by registry_load_upstreams as "host|port|remote|username|password".
REGISTRY_UPSTREAMS=()

registry_container_for_port() {
  printf 'gpu-offload-cache-%s' "$1"
}

# Read upstreams.conf, expand environment references, and drop rows whose host
# still has an empty expansion. Those rows describe optional registries, such as
# an Azure Container Registry whose name is supplied by the environment.
registry_load_upstreams() {
  REGISTRY_UPSTREAMS=()
  [[ -f "$REGISTRY_UPSTREAMS_FILE" ]] || fatal "No upstream table at $REGISTRY_UPSTREAMS_FILE"

  local host port remote username_env password_env username password
  while read -r host port remote username_env password_env; do
    [[ -z "$host" || "$host" == \#* ]] && continue

    host="$(envsubst <<< "$host")"
    remote="$(envsubst <<< "$remote")"
    # An unset expansion leaves the fixed part of the hostname behind, so test
    # for the leading dot rather than for an entirely empty value.
    [[ "$host" == .* || -z "$host" ]] && continue

    username=""
    password=""
    if [[ "$username_env" != "-" ]]; then
      username="${!username_env:-}"
    fi
    if [[ "$password_env" != "-" ]]; then
      password="${!password_env:-}"
    fi

    REGISTRY_UPSTREAMS+=("$host|$port|$remote|$username|$password")
  done < "$REGISTRY_UPSTREAMS_FILE"
}

registry_render_k3s_config() {
  printf 'mirrors:\n'
  printf '  "%s":\n    endpoint:\n      - "http://%s"\n' "$REGISTRY_HOST" "$REGISTRY_HOST"
  local spec host port
  for spec in "${REGISTRY_UPSTREAMS[@]}"; do
    IFS='|' read -r host port _ _ _ <<< "$spec"
    printf '  "%s":\n    endpoint:\n      - "http://localhost:%s"\n' "$host" "$port"
  done
  printf 'configs:\n'
  printf '  "%s":\n    tls:\n      insecure_skip_verify: true\n' "$REGISTRY_HOST"
  for spec in "${REGISTRY_UPSTREAMS[@]}"; do
    IFS='|' read -r _ port _ _ _ <<< "$spec"
    printf '  "localhost:%s":\n    tls:\n      insecure_skip_verify: true\n' "$port"
  done
}

registry_render_podman_config() {
  printf '# Managed by gpu-offload/registry/registry-up.sh\n'
  printf 'unqualified-search-registries = ["docker.io"]\n\n'
  printf '[[registry]]\nprefix = "%s"\nlocation = "%s"\ninsecure = true\n\n' "$REGISTRY_HOST" "$REGISTRY_HOST"
  local spec host port
  for spec in "${REGISTRY_UPSTREAMS[@]}"; do
    IFS='|' read -r host port _ _ _ <<< "$spec"
    # A mirror rather than a location rewrite, so podman falls back to the
    # upstream when a cache is stopped.
    printf '[[registry]]\nprefix = "%s"\nlocation = "%s"\n\n' "$host" "$host"
    printf '[[registry.mirror]]\nlocation = "localhost:%s"\ninsecure = true\n\n' "$port"
  done
}

registry_is_running() {
  podman container exists "$1" \
    && [[ "$(podman inspect --format '{{.State.Running}}' "$1")" == "true" ]]
}

registry_wait_until_answering() {
  local endpoint="$1" _attempt
  for _attempt in $(seq 1 30); do
    # A cache that requires credentials answers /v2/ with 401, which still
    # proves the listener is up, so any HTTP response counts as ready.
    if curl --silent --output /dev/null --max-time 2 "http://$endpoint/v2/"; then
      return 0
    fi
    sleep 1
  done
  return 1
}
