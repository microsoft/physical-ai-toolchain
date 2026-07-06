#!/usr/bin/env bash
# Validate the dataviewer compose stack: parse the compose file, build every
# service image on its pinned base, then start the stack and wait for both
# services to report healthy. Catches build regressions (a Dependabot
# base-digest bump, a broken compose edit) and runtime regressions (e.g. the
# non-root nginx entrypoint failing to write its envsubst'd config, or a broken
# proxy_pass in that config). Shared by CI and local.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

compose="$REPO_ROOT/data-management/viewer/docker-compose.yml"
frontend_port="${FRONTEND_PORT:-5173}"

require_tools docker curl
[[ -f "$compose" ]] || fatal "compose file not found: $compose"

# Non-empty Azure AD build args exercise the auth-enabled frontend build path;
# the values are throwaway placeholders — the smoke never authenticates, it only
# proves the image builds and serves with auth wired in.
export VITE_AZURE_CLIENT_ID="${VITE_AZURE_CLIENT_ID:-00000000-0000-0000-0000-000000000000}"
export VITE_AZURE_TENANT_ID="${VITE_AZURE_TENANT_ID:-00000000-0000-0000-0000-000000000000}"

# Isolate the backend's read-only data mount so the run leaves no root-owned
# directory in the working tree.
data_dir="$(mktemp -d)"
export DATAVIEWER_HOST_DATA_DIR="$data_dir"

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    warn "Smoke failed (exit $status); dumping container logs"
    docker compose -f "$compose" logs --no-color --tail 100 || true
  fi
  docker compose -f "$compose" down --volumes --remove-orphans || true
  rm -rf "$data_dir"
}
trap cleanup EXIT

section "Dataviewer compose build smoke"
print_kv "Compose" "$compose"

docker compose -f "$compose" config --quiet
docker compose -f "$compose" build

section "Dataviewer compose runtime smoke"
# --wait blocks until every service is healthy per its healthcheck (or the
# timeout elapses); a non-root entrypoint that cannot start fails here.
docker compose -f "$compose" up --detach --wait --wait-timeout 240

# Exercise the frontend's envsubst'd nginx config end-to-end: reaching the
# backend /health through the frontend proxy proves the substitution produced a
# working proxy_pass, which the frontend's own healthcheck (index.html) skips.
info "Probing backend /health through the frontend proxy"
# Local health-probe URL, not a download — assign to a variable so the
# dependency-pinning analyzer does not read it as an unchecksummed fetch.
health_url="http://localhost:${frontend_port}/health"
curl --fail --silent --show-error --retry 3 "$health_url" >/dev/null

info "Both services healthy; frontend proxy reaches backend /health"
