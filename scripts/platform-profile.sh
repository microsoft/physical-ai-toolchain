#!/usr/bin/env bash
# Print the resolved platform profile for tools that cannot source the shell library.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
# shellcheck source=lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Print the platform profile that OSMO submit scripts and e2e fixtures use.
PLATFORM_PROFILE selects the profile when --profile is omitted (default: azure).

OPTIONS:
    -h, --help         Show this help message
    -p, --profile NAME Profile to resolve: azure or local
    --json             Print a JSON object instead of KEY=VALUE lines

EXAMPLES:
    $(basename "$0") --profile local
    PLATFORM_PROFILE=local $(basename "$0") --json
EOF
}

profile=""
output_json=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)    show_help; exit 0 ;;
    -p|--profile) profile="$2"; shift 2 ;;
    --json)       output_json=true; shift ;;
    *)            fatal "Unknown option: $1" ;;
  esac
done

load_platform_profile "$profile"

if [[ "$output_json" == "true" ]]; then
  require_tools jq
  platform_profile_env | jq -R -s '
    split("\n") | map(select(length > 0) | capture("^(?<key>[^=]+)=(?<value>.*)$")) | from_entries'
else
  platform_profile_env
fi
