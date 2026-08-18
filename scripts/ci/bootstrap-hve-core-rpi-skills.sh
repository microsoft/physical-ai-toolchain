#!/usr/bin/env bash
# Provision the pinned hve-core RPI skill suite for cloud-agent sessions.
# This bootstrap remains standalone because it establishes the agent's trusted
# instruction set before repository helpers are loaded.
set -o errexit -o nounset -o pipefail

UPSTREAM_REPO="${UPSTREAM_REPO:-microsoft/hve-core}"
UPSTREAM_REF="${UPSTREAM_REF:-}"
UPSTREAM_SKILLS_PATH="${UPSTREAM_SKILLS_PATH:-.github/skills/rpi}"
# DEST_DIR may relocate the workspace root, but the discovery leaf must remain .github/skills/rpi.
DEST_DIR="${DEST_DIR:-.github/skills/rpi}"

required_skills=(
    rpi-quick
    rpi-research
    rpi-plan
    rpi-implement
    rpi-review
    rpi-challenger
    rpi-plan-critique
    rpi-walkthrough
)

fail() {
    echo "$*" >&2
    exit 1
}

require_tools() {
    local tool
    for tool in "$@"; do
        command -v "$tool" >/dev/null 2>&1 || fail "Required tool not found: ${tool}"
    done
}

gh_api_with_retry() {
    local endpoint="$1"
    local attempt
    local max_attempts=3
    local output_file
    local error_file

    output_file="$(mktemp)"
    error_file="$(mktemp)"
    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        if gh api "$endpoint" >"$output_file" 2>"$error_file"; then
            cat "$output_file"
            rm -f "$output_file" "$error_file"
            return 0
        fi
        printf 'gh api attempt %d/%d failed for %s: ' "$attempt" "$max_attempts" "$endpoint" >&2
        cat "$error_file" >&2
        if [ "$attempt" -lt "$max_attempts" ]; then
            sleep "$attempt"
        fi
    done

    rm -f "$output_file" "$error_file"
    return 1
}

require_tools curl gh git jq mktemp
[ -n "$UPSTREAM_REF" ] || fail "UPSTREAM_REF is required"
UPSTREAM_SKILLS_PATH="${UPSTREAM_SKILLS_PATH%/}"
[ -n "$UPSTREAM_SKILLS_PATH" ] || fail "UPSTREAM_SKILLS_PATH is required"

dest_parent="$(dirname "$DEST_DIR")"
mkdir -p "$dest_parent"
dest_parent="$(cd "$dest_parent" && pwd)"
dest_abs="${dest_parent}/$(basename "$DEST_DIR")"
case "$dest_abs" in
*/.github/skills/rpi) ;;
*) fail "Refusing nonstandard RPI destination: ${dest_abs}" ;;
esac

commit_json="$(gh_api_with_retry "repos/${UPSTREAM_REPO}/commits/${UPSTREAM_REF}")" ||
    fail "Failed to resolve ${UPSTREAM_REPO}@${UPSTREAM_REF}"
sha="$(jq -r '.sha // empty' <<<"$commit_json")"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "Invalid resolved SHA for ${UPSTREAM_REPO}@${UPSTREAM_REF}: ${sha}"

echo "Resolved ${UPSTREAM_REPO}@${UPSTREAM_REF} -> ${sha}"

tree_json="$(gh_api_with_retry "repos/${UPSTREAM_REPO}/git/trees/${sha}?recursive=1")" ||
    fail "Failed to read ${UPSTREAM_REPO}@${sha} tree"
jq -e '.truncated != true' <<<"$tree_json" >/dev/null ||
    fail "Git tree response was truncated for ${UPSTREAM_REPO}@${sha}"

unsupported_files="$(
    jq -r --arg prefix "${UPSTREAM_SKILLS_PATH}/" '
        .tree[]
        | select(.path | startswith($prefix))
        | select(.type != "tree")
        | select(.type != "blob" or .mode != "100644")
        | .path
    ' <<<"$tree_json"
)"
[ -z "$unsupported_files" ] ||
    fail "Unsupported RPI skill file mode or type: ${unsupported_files}"

entries_file="$(mktemp)"
paths_file="$(mktemp)"
staging_dir=""
backup_dir=""
install_complete=false
cleanup() {
    rm -f "$entries_file" "$paths_file"
    if [ -n "$staging_dir" ] && [ -d "$staging_dir" ]; then
        rm -rf "$staging_dir"
    fi
    if [ "$install_complete" != true ] && [ -n "$backup_dir" ] && [ -e "$backup_dir" ]; then
        if [ ! -e "$dest_abs" ]; then
            mv "$backup_dir" "$dest_abs"
        fi
    fi
}
trap cleanup EXIT

jq -r --arg prefix "${UPSTREAM_SKILLS_PATH}/" '
    .tree[]
    | select(.type == "blob" and .mode == "100644")
    | select(.path | startswith($prefix))
    | [.path, .sha] | @tsv
' <<<"$tree_json" >"$entries_file"

[ -s "$entries_file" ] ||
    fail "No RPI skill files discovered under ${UPSTREAM_REPO}@${sha}:${UPSTREAM_SKILLS_PATH}"
cut -f1 "$entries_file" >"$paths_file"

for skill in "${required_skills[@]}"; do
    required_path="${UPSTREAM_SKILLS_PATH}/${skill}/SKILL.md"
    grep -Fxq "$required_path" "$paths_file" ||
        fail "Missing required RPI skill: ${skill}"
done

staging_dir="$(mktemp -d "${dest_abs}.staging.XXXXXX")"
install_root="${staging_dir}/rpi"
mkdir -p "$install_root"

while IFS=$'\t' read -r path blob_sha; do
    relative_path="${path#"${UPSTREAM_SKILLS_PATH}/"}"
    case "$relative_path" in
    '' | /* | .. | ../* | */../* | */..) fail "Unsafe RPI skill path: ${path}" ;;
    *.md) ;;
    *) fail "Unsupported RPI skill file type: ${path}" ;;
    esac
    [[ "$relative_path" =~ ^[A-Za-z0-9._/-]+$ ]] ||
        fail "Unsafe characters in RPI skill path: ${path}"

    destination="${install_root}/${relative_path}"
    mkdir -p "$(dirname "$destination")"
    # pinning-ignore: content is verified against the pinned Git blob SHA immediately below.
    curl --fail --silent --show-error --location \
        --retry 3 --retry-delay 2 --retry-all-errors \
        "https://raw.githubusercontent.com/${UPSTREAM_REPO}/${sha}/${path}" \
        --output "$destination"

    actual_blob_sha="$(git hash-object "$destination")"
    [ "$actual_blob_sha" = "$blob_sha" ] ||
        fail "RPI skill integrity check failed for ${path}: expected ${blob_sha}, got ${actual_blob_sha}"
done <"$entries_file"

for skill in "${required_skills[@]}"; do
    [ -s "${install_root}/${skill}/SKILL.md" ] ||
        fail "Installed RPI skill is missing or empty: ${skill}"
done

files_json="$(jq -R -s 'split("\n") | map(select(length > 0))' <"$paths_file")"
jq -n \
    --arg upstream_repo "$UPSTREAM_REPO" \
    --arg requested_ref "$UPSTREAM_REF" \
    --arg resolved_sha "$sha" \
    --arg resolved_at "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    --argjson files "$files_json" \
    '{
      upstream_repo: $upstream_repo,
      requested_ref: $requested_ref,
      resolved_sha: $resolved_sha,
      resolved_at: $resolved_at,
      files: $files
    }' >"${install_root}/_audit.json"

backup_dir="${dest_abs}.backup.$$"
if [ -e "$dest_abs" ]; then
    mv "$dest_abs" "$backup_dir"
fi
if ! mv "$install_root" "$dest_abs"; then
    if [ -e "$backup_dir" ]; then
        mv "$backup_dir" "$dest_abs"
    fi
    fail "Failed to install RPI skills into ${dest_abs}"
fi
rm -rf "$backup_dir"
backup_dir=""
install_complete=true

installed_count="${#required_skills[@]}"
file_count="$(wc -l <"$paths_file" | tr -d ' ')"
echo "Installed ${installed_count} RPI skills and ${file_count} verified files from ${UPSTREAM_REPO}@${sha}"
