#!/usr/bin/env bash
# Install the pinned actionlint binary used by workflow linting.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
    cat << EOF
Usage: $(basename "$0")

Install actionlint ${ACTIONLINT_VERSION} into /usr/local/bin.

OPTIONS:
    -h, --help    Show this help message
EOF
}

ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-1.7.12}"
ACTIONLINT_ARCH=""
ACTIONLINT_SHA256=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) show_help; exit 0 ;;
        *) fatal "Unknown option: $1" ;;
    esac
done

[[ "$(uname -s)" == "Linux" ]] || fatal "actionlint installer supports Linux only"

require_tools curl sha256sum sudo tar

case "$(uname -m)" in
    x86_64)
        ACTIONLINT_ARCH="amd64"
        ACTIONLINT_SHA256="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        ;;
    aarch64 | arm64)
        ACTIONLINT_ARCH="arm64"
        ACTIONLINT_SHA256="325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6"
        ;;
    *)
        fatal "Unsupported architecture: $(uname -m)"
        ;;
esac

section "Install actionlint"
print_kv "Version" "$ACTIONLINT_VERSION"
print_kv "Architecture" "$ACTIONLINT_ARCH"

archive="/tmp/actionlint.tar.gz"
curl -sSfL "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_${ACTIONLINT_ARCH}.tar.gz" -o "$archive"
echo "${ACTIONLINT_SHA256}  ${archive}" | sha256sum -c --quiet -
sudo tar -xzf "$archive" -C /usr/local/bin actionlint
rm -f "$archive"

actionlint -version
