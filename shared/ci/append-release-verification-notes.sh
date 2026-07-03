#!/usr/bin/env bash
# Append artifact verification instructions to GitHub release notes.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
    cat << EOF
Usage: $(basename "$0")

Append artifact verification instructions to GitHub release notes.

Required environment variables:
    TAG                  Release tag to update
    GITHUB_REPOSITORY    Repository in owner/name format
    GH_TOKEN             GitHub token with release write access

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

require_tools gh

tag="${TAG:?TAG is required}"
repository="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

body="$(gh release view "$tag" --repo "$repository" --json body --jq '.body')"

verification="$(cat <<'EOF'

---

## Artifact Verification

All release artifacts include [Sigstore](https://www.sigstore.dev/) provenance attestations. Verify with the [GitHub CLI](https://cli.github.com/):

```bash
# Download the source archive
gh release download TAG_PLACEHOLDER --repo microsoft/physical-ai-toolchain --pattern 'source-TAG_PLACEHOLDER.tar.gz'

# Verify build provenance
gh attestation verify source-TAG_PLACEHOLDER.tar.gz --repo microsoft/physical-ai-toolchain

# Verify SBOM attestation
gh attestation verify source-TAG_PLACEHOLDER.tar.gz --repo microsoft/physical-ai-toolchain --predicate-type https://spdx.dev/Document

# Download and verify the signed wheel (identity ends @refs/heads/main: the pipeline runs on push to main)
gh release download TAG_PLACEHOLDER --repo microsoft/physical-ai-toolchain --pattern '*.whl' --pattern '*.whl.sigstore.json'
sigstore verify identity *.whl --bundle *.whl.sigstore.json --cert-identity 'https://github.com/microsoft/physical-ai-toolchain/.github/workflows/main.yml@refs/heads/main' --cert-oidc-issuer 'https://token.actions.githubusercontent.com'

# Download the CycloneDX SBOMs (SPDX equivalents also attached)
gh release download TAG_PLACEHOLDER --repo microsoft/physical-ai-toolchain --pattern 'sbom.cdx.json' --pattern 'dependencies.cdx.json'
```

EOF
)"

verification="${verification//TAG_PLACEHOLDER/${tag}}"
printf '%s\n%s\n' "$body" "$verification" > notes.md
gh release edit "$tag" --repo "$repository" --notes-file notes.md

section "Release notes"
print_kv "Tag" "$tag"
info "Verification instructions appended"
