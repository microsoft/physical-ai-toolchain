#!/usr/bin/env bash
# Enable GitHub auto-merge for an eligible Dependabot pull request.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
# shellcheck source=scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Enable GitHub auto-merge with squash for an eligible Dependabot pull request.

OPTIONS:
    -h, --help               Show this help message
        --pr-url URL         Pull request URL (default: PR_URL environment variable)
        --config-preview     Print configuration and exit

EXAMPLES:
    $(basename "$0") --pr-url https://github.com/org/repo/pull/123
EOF
}

pr_url="${PR_URL:-}"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --pr-url)          pr_url="$2"; shift 2 ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "PR URL" "${pr_url:-<unset>}"
  exit 0
fi

[[ -n "$pr_url" ]] || fatal "Pull request URL required via --pr-url or PR_URL"

require_tools gh grep

#------------------------------------------------------------------------------
# Main Logic
#------------------------------------------------------------------------------

section "Enable Auto-merge"

status="armed"
if out=$(gh pr merge --auto --squash "$pr_url" 2>&1); then
  info "Auto-merge armed: $pr_url"
elif grep -qi "auto.merge is not allowed" <<<"$out"; then
  status="skipped"
  echo "::notice::'Allow auto-merge' is off; no-op until an admin enables it."
else
  error "$out"
  exit 1
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "PR URL" "$pr_url"
print_kv "Status" "$status"
