#!/usr/bin/env bash
# Run the OSMO CLI from a local source checkout via Bazel
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OSMO_CLI_ARGS...]

Build and run the OSMO CLI from a local source checkout using Bazel.

Requires OSMO_SOURCE_DIR to point to a cloned OSMO repository.
Set it in .env.local at the repository root or export it as an environment variable.

ENVIRONMENT:
    OSMO_SOURCE_DIR    Absolute path to the OSMO repository clone (required)

SETUP:
    1. Clone the OSMO repository:
       git clone https://github.com/NVIDIA/OSMO.git ~/osmo

    2. Configure OSMO_SOURCE_DIR (choose one):
       a. Add to .env.local (recommended):
          echo 'OSMO_SOURCE_DIR=~/osmo' >> .env.local

       b. Export as environment variable:
          export OSMO_SOURCE_DIR=~/osmo

    3. Install Bazel:
       brew install bazel    (macOS)
       https://bazel.build/install

EXAMPLES:
    $(basename "$0") login http://10.0.5.7 --method=dev --username=testuser
    $(basename "$0") info
    $(basename "$0") workflow list
    $(basename "$0") version
    $(basename "$0") backend list

EOF
}

case "${1:-}" in
  -h|--help) show_help; exit 0 ;;
esac

# Source .env.local from repo root for OSMO_SOURCE_DIR
_repo_root="$(cd "$SCRIPT_DIR/../../.." && pwd)"
_env_local="$_repo_root/.env.local"
if [[ -f "$_env_local" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$_env_local"
  set +a
fi
unset _repo_root _env_local

if [[ -z "${OSMO_SOURCE_DIR:-}" ]]; then
  echo "[ERROR] OSMO_SOURCE_DIR is not set." >&2
  echo "" >&2
  echo "Set it in .env.local at the repository root or export it:" >&2
  echo "  echo 'OSMO_SOURCE_DIR=~/path/to/OSMO' >> .env.local" >&2
  echo "" >&2
  echo "Run '$(basename "$0") --help' for setup instructions." >&2
  exit 1
fi

# Expand ~ to $HOME
OSMO_SOURCE_DIR="${OSMO_SOURCE_DIR/#\~/$HOME}"

if [[ ! -d "$OSMO_SOURCE_DIR" ]]; then
  echo "[ERROR] OSMO_SOURCE_DIR directory does not exist: $OSMO_SOURCE_DIR" >&2
  exit 1
fi

if [[ ! -f "$OSMO_SOURCE_DIR/MODULE.bazel" ]]; then
  echo "[ERROR] Not a valid OSMO repository (MODULE.bazel not found): $OSMO_SOURCE_DIR" >&2
  exit 1
fi

if ! command -v bazel >/dev/null 2>&1; then
  echo "[ERROR] Missing required tool: bazel" >&2
  echo "Install via: brew install bazel (macOS) or https://bazel.build/install" >&2
  exit 1
fi

cd "$OSMO_SOURCE_DIR"
exec bazel run @osmo_workspace//src/cli -- "$@"
