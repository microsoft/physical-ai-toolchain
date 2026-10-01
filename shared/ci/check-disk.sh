#!/usr/bin/env bash
# Report free space on the root filesystem and flag when it falls below the
# runtime-image smoke budget. Writes low=true|false to GITHUB_OUTPUT so CI can
# run the slow disk cleanup only when it is needed. Shared by CI and local runs.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Report free space on a filesystem and flag when it is below the minimum needed
for a runtime-image smoke. Writes low=true|false to \$GITHUB_OUTPUT when set.

OPTIONS:
    -m, --min-gib GIB  Minimum free space in GiB (default: $min_gib)
    -p, --path PATH    Filesystem path to check (default: $path)
    -h, --help         Show this help message

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --min-gib 60
EOF
}

# The PyTorch runtime image plus its locked dependencies uses roughly 20 GiB.
min_gib="${SMOKE_MIN_FREE_GIB:-40}"
path="/"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) show_help; exit 0 ;;
        -m | --min-gib) min_gib="$2"; shift 2 ;;
        -p | --path) path="$2"; shift 2 ;;
        *) fatal "Unknown option: $1" ;;
    esac
done

[[ "$min_gib" =~ ^[0-9]+$ ]] || fatal "Invalid --min-gib: $min_gib (expected a whole number)"

require_tools df

avail_gib="$(df --output=avail -BG "$path" | tail -n 1 | tr -dc '0-9')"
[[ -n "$avail_gib" ]] || fatal "Could not read free space for $path"

low=false
if (( avail_gib < min_gib )); then
    low=true
fi

section "Disk Space"
print_kv "Path" "$path"
print_kv "Available" "${avail_gib} GiB"
print_kv "Minimum" "${min_gib} GiB"
print_kv "Cleanup needed" "$low"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    echo "low=$low" >> "$GITHUB_OUTPUT"
fi
