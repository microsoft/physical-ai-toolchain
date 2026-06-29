#!/usr/bin/env bash
# Validate the dataviewer compose stack: parse the compose file and build every
# service image on its pinned base, so a Dependabot base-digest bump (or a broken
# compose edit) is caught before it can auto-merge. Build is the gate; no app
# tests run here. Shared by CI and local.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

compose="$REPO_ROOT/data-management/viewer/docker-compose.yml"

require_tools docker
[[ -f "$compose" ]] || fatal "compose file not found: $compose"

section "Dataviewer compose build smoke"
print_kv "Compose" "$compose"

docker compose -f "$compose" config --quiet
docker compose -f "$compose" build
