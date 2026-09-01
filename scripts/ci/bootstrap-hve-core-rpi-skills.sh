#!/usr/bin/env bash
# Provision the pinned hve-core RPI skill suite for cloud-agent sessions.
# cspell:ignore unmatch
set -o errexit -o nounset -o pipefail

UPSTREAM_REPO="${UPSTREAM_REPO:-microsoft/hve-core}"
UPSTREAM_REF="${UPSTREAM_REF:-}"
UPSTREAM_SKILLS_PATH="${UPSTREAM_SKILLS_PATH:-.github/skills/rpi}"
SKILLS_ROOT="${SKILLS_ROOT:-.github/skills}"
AUDIT_FILE=".rpi-audit.json"

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
artifacts=("${required_skills[@]}" "$AUDIT_FILE")

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
[[ "$UPSTREAM_REF" =~ ^[0-9a-f]{40}$ ]] || fail "UPSTREAM_REF must be a 40-character lowercase commit SHA"
UPSTREAM_SKILLS_PATH="${UPSTREAM_SKILLS_PATH%/}"
[ -n "$UPSTREAM_SKILLS_PATH" ] || fail "UPSTREAM_SKILLS_PATH is required"

skills_parent="$(dirname "$SKILLS_ROOT")"
mkdir -p "$skills_parent"
skills_parent="$(cd "$skills_parent" && pwd)"
skills_root_abs="${skills_parent}/$(basename "$SKILLS_ROOT")"
case "$skills_root_abs" in
*/.github/skills) ;;
*) fail "Refusing nonstandard skills root: ${skills_root_abs}" ;;
esac
mkdir -p "$skills_root_abs"

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$repo_root" ]; then
    for skill in "${required_skills[@]}"; do
        if git -C "$repo_root" ls-files --error-unmatch ".github/skills/${skill}" >/dev/null 2>&1; then
            fail "Refusing to replace tracked RPI skill: .github/skills/${skill}"
        fi
    done
fi

commit_json="$(gh_api_with_retry "repos/${UPSTREAM_REPO}/commits/${UPSTREAM_REF}")" ||
    fail "Failed to resolve ${UPSTREAM_REPO}@${UPSTREAM_REF}"
sha="$(jq -r '.sha // empty' <<<"$commit_json")"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "Invalid resolved SHA for ${UPSTREAM_REPO}@${UPSTREAM_REF}: ${sha}"
[ "$sha" = "$UPSTREAM_REF" ] || fail "Resolved SHA does not match immutable UPSTREAM_REF: ${sha}"

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
installed_artifacts=()
install_started=false
install_complete=false
cleanup() {
    local artifact
    local restore_failed=false

    rm -f "$entries_file" "$paths_file"
    if [ -n "$staging_dir" ] && [ -d "$staging_dir" ]; then
        rm -rf "$staging_dir"
    fi
    if [ "$install_started" = true ] && [ "$install_complete" != true ]; then
        if [ "${#installed_artifacts[@]}" -gt 0 ]; then
            for artifact in "${installed_artifacts[@]}"; do
                rm -rf "${skills_root_abs:?}/${artifact}"
            done
        fi
        for artifact in "${artifacts[@]}"; do
            if [ -e "${backup_dir}/${artifact}" ]; then
                if ! mv "${backup_dir}/${artifact}" "${skills_root_abs}/${artifact}"; then
                    echo "Failed to restore ${artifact}; backup preserved at ${backup_dir}" >&2
                    restore_failed=true
                fi
            fi
        done
    fi
    if [ -n "$backup_dir" ] && [ -d "$backup_dir" ] && [ "$restore_failed" = false ]; then
        rm -rf "$backup_dir"
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

while IFS=$'\t' read -r path _; do
    relative_path="${path#"${UPSTREAM_SKILLS_PATH}/"}"
    case "$relative_path" in
    '' | /* | .. | ../* | */../* | */..) fail "Unsafe RPI skill path: ${path}" ;;
    *.md) ;;
    *) fail "Unsupported RPI skill file type: ${path}" ;;
    esac
    [[ "$relative_path" =~ ^[A-Za-z0-9._/-]+$ ]] ||
        fail "Unsupported characters in RPI skill path: ${path}"

    top_level="${relative_path%%/*}"
    is_required=false
    for skill in "${required_skills[@]}"; do
        if [ "$top_level" = "$skill" ]; then
            is_required=true
            break
        fi
    done
    [ "$is_required" = true ] ||
        fail "Unexpected top-level RPI skill path: ${path}"
done <"$entries_file"

for skill in "${required_skills[@]}"; do
    required_path="${UPSTREAM_SKILLS_PATH}/${skill}/SKILL.md"
    grep -Fxq "$required_path" "$paths_file" ||
        fail "Missing required RPI skill: ${skill}"
done

staging_dir="$(mktemp -d "${skills_root_abs}/.rpi-staging.XXXXXX")"

while IFS=$'\t' read -r path blob_sha; do
    relative_path="${path#"${UPSTREAM_SKILLS_PATH}/"}"
    destination="${staging_dir}/${relative_path}"
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
    [ -s "${staging_dir}/${skill}/SKILL.md" ] ||
        fail "Installed RPI skill is missing or empty: ${skill}"
done

files_json="$(jq -Rn '[inputs | split("\t") | {path: .[0], blob_sha: .[1]}]' <"$entries_file")"
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
    }' >"${staging_dir}/${AUDIT_FILE}"

backup_dir="$(mktemp -d "${skills_root_abs}/.rpi-backup.XXXXXX")"
install_started=true
for artifact in "${artifacts[@]}"; do
    if [ -e "${skills_root_abs}/${artifact}" ]; then
        mv "${skills_root_abs}/${artifact}" "${backup_dir}/${artifact}"
    fi
done

for artifact in "${artifacts[@]}"; do
    if ! mv "${staging_dir}/${artifact}" "${skills_root_abs}/${artifact}"; then
        fail "Failed to install RPI artifact into ${skills_root_abs}/${artifact}"
    fi
    installed_artifacts+=("$artifact")
done
install_complete=true
rm -rf "$backup_dir"
backup_dir=""

installed_count="${#required_skills[@]}"
file_count="$(wc -l <"$paths_file" | tr -d ' ')"
echo "Installed ${installed_count} RPI skills and ${file_count} verified files from ${UPSTREAM_REPO}@${sha}"
