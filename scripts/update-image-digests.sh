#!/usr/bin/env bash
# Refresh @sha256 digest pins for the OCI container images referenced across the
# repository's OSMO/AzureML workflow templates, Helm values, Kubernetes manifests,
# and the shared submission defaults in scripts/lib/common.sh.
#
# References are discovered automatically: every "<image>:<tag>@sha256:<digest>" is
# re-resolved to its current registry digest and rewritten in place. Dockerfiles,
# compose files, and .github/ are skipped because those digests are owned by
# Dependabot and the gh-aw workflow compiler respectively.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
# shellcheck source=scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Discover every "<image>:<tag>@sha256:<digest>" reference in the repository, resolve
each tag to its current registry digest, and rewrite the pins in place. Dockerfiles,
compose files, and .github/ are skipped (Dependabot and the gh-aw compiler own those).
AzureML environment references (azureml:<name>:latest) are not digest pins and are
left untouched.

OPTIONS:
    -h, --help               Show this help message
    --dry-run                Show what would change without writing
    --config-preview         Print the discovered images and files, then exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --dry-run
EOF
}

# Defaults
dry_run=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --dry-run)         dry_run=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools git curl jq

# Reference shape: registry/repo:tag@sha256:<64 hex>. Pathspecs exclude the files
# whose digests are maintained by other tooling (Dependabot, gh-aw).
digest_ref_re='[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}'
exclude_paths=(
  ':!.github'
  ':(exclude,glob)**/Dockerfile*'
  ':(exclude,glob)**/*compose*.y*ml'
  ':(exclude,glob)**/tests/Fixtures/**'
  ':(exclude,glob)**/*.Tests.ps1'
)

cd "$REPO_ROOT"

#------------------------------------------------------------------------------
# Helper Functions
#------------------------------------------------------------------------------

# Fetch the manifest response headers for an image, acquiring an anonymous pull
# token for registries that require one. Prints headers to stdout (empty on fail).
fetch_manifest_headers() {
  local host="$1" repo="$2" tag="$3" token="" token_url="" manifest_url
  local accept='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json,application/vnd.oci.image.manifest.v1+json'
  # Registry API metadata calls (anonymous pull token + manifest HEAD), not
  # artifact downloads: the sha256 digest is itself the integrity value being
  # resolved here, so there is nothing to checksum.
  case "$host" in
    registry-1.docker.io) token_url="https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" ;;
    nvcr.io)              token_url="https://nvcr.io/proxy_auth?scope=repository:${repo}:pull" ;;
  esac
  if [[ -n "$token_url" ]]; then
    token=$(curl -fsSL "$token_url" 2>/dev/null | jq -r '.token // .access_token // empty' 2>/dev/null || echo "")
  fi
  manifest_url="https://${host}/v2/${repo}/manifests/${tag}"
  if [[ -n "$token" ]]; then
    curl -fsSL -o /dev/null -D - -H "Authorization: Bearer $token" -H "Accept: $accept" "$manifest_url" 2>/dev/null || true
  else
    curl -fsSL -o /dev/null -D - -H "Accept: $accept" "$manifest_url" 2>/dev/null || true
  fi
}

# Resolve "registry/repo:tag" to its immutable sha256 digest (the value docker
# pull matches), or print nothing on failure.
resolve_digest() {
  local ref="$1" host repo tag
  case "$ref" in
    nvcr.io/*)         host="nvcr.io";             repo="${ref#nvcr.io/}" ;;
    registry.k8s.io/*) host="registry.k8s.io";     repo="${ref#registry.k8s.io/}" ;;
    *.*/*)             host="${ref%%/*}";          repo="${ref#*/}" ;; # any dotted-host registry (ghcr.io, quay.io, *.azurecr.io, ...)
    *)                 host="registry-1.docker.io"; repo="$ref" ;;
  esac
  tag="${repo##*:}"
  repo="${repo%:*}"
  # Docker Hub official images live under the implicit library/ namespace.
  [[ "$host" == "registry-1.docker.io" && "$repo" != */* ]] && repo="library/$repo"
  # Tolerate the non-zero pipe status from head/no-match under pipefail; the caller validates.
  fetch_manifest_headers "$host" "$repo" "$tag" \
    | tr -d '\r' | grep -i '^docker-content-digest:' | head -n 1 | awk '{ print $2 }' || true
}

#------------------------------------------------------------------------------
# Discover
#------------------------------------------------------------------------------
section "Discovering Digest Pins"

refs=$(git grep -hoE "$digest_ref_re" -- "${exclude_paths[@]}" 2>/dev/null \
  | sed -E 's/@sha256:[0-9a-f]{64}//' | sort -u || true)
files=$(git grep -lE "$digest_ref_re" -- "${exclude_paths[@]}" 2>/dev/null || true)
[[ -n "$refs" ]] || fatal "No digest-pinned image references found under $REPO_ROOT"

ref_count=$(printf '%s\n' "$refs" | grep -c .)
file_count=$(printf '%s\n' "$files" | grep -c .)
print_kv "Images Discovered" "$ref_count"
print_kv "Files Discovered"  "$file_count"

if [[ "$config_preview" == "true" ]]; then
  section "Discovered Images"
  while IFS= read -r ref; do print_kv "Image" "$ref"; done <<< "$refs"
  section "Discovered Files"
  while IFS= read -r file; do print_kv "File" "$file"; done <<< "$files"
  exit 0
fi

#------------------------------------------------------------------------------
# Resolve
#------------------------------------------------------------------------------
section "Resolving Digests"

digest_map="$(mktemp)"
tmp="$(mktemp)"
tmp_new="$(mktemp)"
trap 'rm -f "$digest_map" "$tmp" "$tmp_new"' EXIT
while IFS= read -r ref; do
  digest=$(resolve_digest "$ref")
  [[ "$digest" == sha256:* ]] || fatal "Could not resolve digest for $ref"
  printf '%s %s\n' "$ref" "$digest" >> "$digest_map"
  print_kv "$ref" "$digest"
done <<< "$refs"

#------------------------------------------------------------------------------
# Apply
#------------------------------------------------------------------------------
section "Updating Pins"

updated=0
while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  cp "$file" "$tmp"
  while IFS=' ' read -r ref digest; do
    # Only '.' is an ERE metacharacter in these refs; '#' is the sed delimiter.
    ref_re="$(printf '%s' "$ref" | sed 's/\./\\./g')"
    # Left-anchor on line start or a non-ref char (\1 re-emits it) so a short ref
    # cannot match inside a longer one (e.g. bar:1 within foo/bar:1).
    sed -E "s#(^|[^A-Za-z0-9._/-])(${ref_re})@sha256:[0-9a-f]{64}#\1\2@${digest}#g" "$tmp" > "$tmp_new"
    mv "$tmp_new" "$tmp"
  done < "$digest_map"

  if cmp -s "$file" "$tmp"; then
    continue
  fi
  updated=$((updated + 1))
  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] Would update $file"
    diff -u "$file" "$tmp" || true
  else
    # Overwrite in place (cp keeps the target's own permissions, unlike mv from the 0600 mktemp file).
    cp "$tmp" "$file"
    info "Updated $file"
  fi
done <<< "$files"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Images Checked" "$ref_count"
print_kv "Files Updated"  "$updated"
print_kv "Dry Run"        "$dry_run"
