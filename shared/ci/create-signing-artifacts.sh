#!/usr/bin/env bash
# Create Sigstore bundle artifacts consumed by release verification.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
    cat << EOF
Usage: $(basename "$0")

Create Sigstore bundle artifacts consumed by release verification.

Required environment variables:
    BUNDLE_PATH          Source archive Sigstore bundle path
    WHEEL_BUNDLE_PATH    Wheel Sigstore bundle path
    TAG                  Release tag

OPTIONS:
    -h, --help           Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) show_help; exit 0 ;;
        *) fatal "Unknown option: $1" ;;
    esac
done

require_tools jq

bundle_path="${BUNDLE_PATH:?BUNDLE_PATH is required}"
wheel_bundle_path="${WHEEL_BUNDLE_PATH:?WHEEL_BUNDLE_PATH is required}"
tag="${TAG:?TAG is required}"

section "Create signing artifacts"
cp "$bundle_path" "source-${tag}.sigstore.json"
jq -c '.dsseEnvelope' "$bundle_path" > "source-${tag}.intoto.jsonl"
cp "$wheel_bundle_path" "wheels-${tag}.sigstore.json"
jq -c '.dsseEnvelope' "$wheel_bundle_path" > "wheels-${tag}.intoto.jsonl"

print_kv "Tag" "$tag"
info "Signing artifacts created"
