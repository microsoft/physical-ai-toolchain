#!/usr/bin/env bash
# cspell:ignore redir unmatch
# Refresh @sha256 digest pins for OCI container images referenced in tracked files.
#
# References are discovered automatically: every "<image>:<tag>@sha256:<digest>" is
# re-resolved to its current registry digest and rewritten in place. Dockerfiles and
# compose files are owned by Dependabot; gh-aw compiled workflows are compiler-owned.
# Test fixtures and Pester test files are also excluded.
# Check mode reports drift without modifying source files, optionally writing SARIF.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
# shellcheck source=scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Discover every "<image>:<tag>@sha256:<digest>" reference in tracked files, resolve each
tag to its current registry digest, and rewrite the pins in place. Dockerfiles, compose
files, gh-aw compiled workflows, test fixtures, and Pester test files are skipped.
AzureML environment references (azureml:<name>:latest) are not digest pins and are
left untouched. Check mode is a CI signal that supports SARIF and exits 2 on drift.
Dry-run mode is a local preview of the changes that write mode would apply.

OPTIONS:
    -h, --help               Show this help message
    --check                  CI check; report drift without writing (exits 2 on drift)
    --dry-run                Preview proposed changes without writing
    --sarif-output FILE      Write drift findings as SARIF (requires --check)
    --config-preview         Print the discovered images and files, then exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --check --sarif-output logs/image-digest-freshness.sarif
    $(basename "$0") --dry-run
EOF
}

# Defaults
check=false
dry_run=false
sarif_output=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
  -h | --help)
    show_help
    exit 0
    ;;
  --check)
    check=true
    shift
    ;;
  --dry-run)
    dry_run=true
    shift
    ;;
  --sarif-output)
    [[ $# -ge 2 && -n "$2" && "$2" != -* ]] ||
      fatal "--sarif-output requires a non-empty file path"
    sarif_output="$2"
    shift 2
    ;;
  --config-preview)
    config_preview=true
    shift
    ;;
  *) fatal "Unknown option: $1" ;;
  esac
done

if [[ "$check" == "true" && "$dry_run" == "true" ]]; then
  fatal "--check and --dry-run cannot be combined"
fi
if [[ -n "$sarif_output" && "$check" != "true" ]]; then
  fatal "--sarif-output requires --check"
fi

require_tools git curl jq

# Reference shape: registry/repo:tag@sha256:<64 hex>. Pathspecs exclude the files
# whose digests are maintained by other tooling (Dependabot, gh-aw).
digest_value_re='sha256:[0-9a-f]{64}'
ref_left_boundary_re='(^|[^A-Za-z0-9._/-])'
digest_ref_re="[A-Za-z0-9][A-Za-z0-9._/-]*:[A-Za-z0-9._-]+@${digest_value_re}"
exclude_paths=(
  ':(exclude,glob).github/workflows/*.lock.yml'
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
  local curl_args=(-fsSL --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 30 --retry 2)
  # Registry API metadata calls (anonymous pull token + manifest HEAD), not
  # artifact downloads: the sha256 digest is itself the integrity value being
  # resolved here, so there is nothing to checksum.
  case "$host" in
  registry-1.docker.io) token_url="https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" ;;
  nvcr.io) token_url="https://nvcr.io/proxy_auth?scope=repository:${repo}:pull" ;;
  esac
  if [[ -n "$token_url" ]]; then
    token=$(curl "${curl_args[@]}" "$token_url" 2>/dev/null | jq -r '.token // .access_token // empty' 2>/dev/null || echo "")
  fi
  manifest_url="https://${host}/v2/${repo}/manifests/${tag}"
  [[ -n "$token" ]] && curl_args+=(-H "Authorization: Bearer $token")
  curl "${curl_args[@]}" -o /dev/null -D - -H "Accept: $accept" "$manifest_url" 2>/dev/null || true
}

# Resolve "registry/repo:tag" to its immutable sha256 digest (the value docker
# pull matches), or print nothing on failure.
resolve_digest() {
  local ref="$1" host repo tag
  case "$ref" in
  nvcr.io/*)
    host="nvcr.io"
    repo="${ref#nvcr.io/}"
    ;;
  registry.k8s.io/*)
    host="registry.k8s.io"
    repo="${ref#registry.k8s.io/}"
    ;;
  *.*/*)
    host="${ref%%/*}"
    repo="${ref#*/}"
    ;; # any dotted-host registry (ghcr.io, quay.io, *.azurecr.io, ...)
  *)
    host="registry-1.docker.io"
    repo="$ref"
    ;;
  esac
  tag="${repo##*:}"
  repo="${repo%:*}"
  # Docker Hub official images live under the implicit library/ namespace.
  [[ "$host" == "registry-1.docker.io" && "$repo" != */* ]] && repo="library/$repo"
  # Tolerate the non-zero pipe status from head/no-match under pipefail; the caller validates.
  fetch_manifest_headers "$host" "$repo" "$tag" |
    tr -d '\r' | grep -i '^docker-content-digest:' | head -n 1 | awk '{ print $2 }' || true
}

escape_ref_re() {
  printf '%s' "$1" | sed 's/\./\\./g'
}

# Record every stale occurrence of a reference using the same left-boundary rule
# as the rewrite expression.
record_drift() {
  local file="$1" ref="$2" ref_re="$3" digest="$4"
  local line_number=0 line_text remaining matched_ref pinned_digest
  local full_match prefix boundary_length start_column end_column consumed next_offset
  local match_re="${ref_left_boundary_re}${ref_re}@${digest_value_re}"

  while IFS= read -r line_text || [[ -n "$line_text" ]]; do
    line_number=$((line_number + 1))
    remaining="$line_text"
    consumed=0
    while [[ "$remaining" =~ $match_re ]]; do
      full_match="${BASH_REMATCH[0]}"
      prefix="${remaining%%"$full_match"*}"
      matched_ref="$full_match"
      boundary_length=0
      if [[ "$matched_ref" != "$ref"* ]]; then
        matched_ref="${matched_ref:1}"
        boundary_length=1
      fi
      pinned_digest="${matched_ref##*@}"
      start_column=$((consumed + ${#prefix} + boundary_length + 1))
      end_column=$((start_column + ${#matched_ref}))
      if [[ "$pinned_digest" != "$digest" ]]; then
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "$file" "$line_number" "$start_column" "$end_column" \
          "$ref" "$pinned_digest" "$digest" >>"$drift_report"
      fi
      next_offset=$((${#prefix} + ${#full_match}))
      if ((next_offset < ${#remaining})); then
        consumed=$((consumed + next_offset - 1))
        remaining="${remaining:$((next_offset - 1))}"
      else
        remaining=""
      fi
    done
  done <"$file"
}

write_sarif() {
  local output="$1" output_dir output_name repo_relative
  if [[ "$output" == */ || -d "$output" ]]; then
    fatal "--sarif-output must name a file, not a directory: $output"
  fi
  output_dir="$(dirname "$output")"
  output_name="$(basename "$output")"
  mkdir -p "$output_dir"
  output_dir="$(cd "$output_dir" && pwd -P)"
  output="$output_dir/$output_name"

  case "$output" in
  "$REPO_ROOT"/*)
    repo_relative="${output#"$REPO_ROOT"/}"
    if git ls-files --error-unmatch -- "$repo_relative" >/dev/null 2>&1; then
      fatal "Refusing to overwrite tracked file with SARIF: $repo_relative"
    fi
    ;;
  esac

  sarif_tmp="$(mktemp "$output_dir/.image-digest-freshness.XXXXXX")"
  if ! jq -Rn '
      [inputs | split("\t") | {
        file: .[0],
        line: (.[1] | tonumber),
        startColumn: (.[2] | tonumber),
        endColumn: (.[3] | tonumber),
        ref: .[4],
        pinned: .[5],
        resolved: .[6]
      }] as $findings
      | {
          version: "2.1.0",
          "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
          runs: [{
            tool: {
              driver: {
                name: "Container Image Digest Freshness",
                informationUri: "https://github.com/microsoft/physical-ai-toolchain",
                rules: [{
                  id: "container-image-digest-drift",
                  shortDescription: {
                    text: "Pinned container image digest differs from the tag digest"
                  },
                  helpUri: "https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-image-digests.sh",
                  defaultConfiguration: { level: "warning" }
                }]
              }
            },
            results: [
              $findings[] | {
                ruleId: "container-image-digest-drift",
                level: "warning",
                message: {
                  text: ("Pinned digest " + .pinned + " for " + .ref
                    + " differs from the registry digest " + .resolved + ".")
                },
                locations: [{
                  physicalLocation: {
                    artifactLocation: { uri: .file },
                    region: {
                      startLine: .line,
                      startColumn: .startColumn,
                      endColumn: .endColumn
                    }
                  }
                }]
              }
            ]
          }]
        }
    ' <"$drift_report" >"$sarif_tmp"; then
    fatal "Failed to render SARIF report: $output"
  fi
  mv "$sarif_tmp" "$output"
  sarif_tmp=""
  sarif_report_path="$output"
}

#------------------------------------------------------------------------------
# Discover
#------------------------------------------------------------------------------
section "Discovering Digest Pins"

refs=$(git grep -hoE "$digest_ref_re" -- "${exclude_paths[@]}" 2>/dev/null |
  sed -E 's/@sha256:[0-9a-f]{64}//' | sort -u || true)
files=$(git grep -lE "$digest_ref_re" -- "${exclude_paths[@]}" 2>/dev/null || true)
[[ -n "$refs" ]] || fatal "No digest-pinned image references found under $REPO_ROOT"

ref_count=$(printf '%s\n' "$refs" | grep -c .)
file_count=$(printf '%s\n' "$files" | grep -c .)
print_kv "Images Discovered" "$ref_count"
print_kv "Files Discovered" "$file_count"

if [[ "$config_preview" == "true" ]]; then
  section "Discovered Images"
  while IFS= read -r ref; do print_kv "Image" "$ref"; done <<<"$refs"
  section "Discovered Files"
  while IFS= read -r file; do print_kv "File" "$file"; done <<<"$files"
  exit 0
fi

#------------------------------------------------------------------------------
# Resolve
#------------------------------------------------------------------------------
section "Resolving Digests"

digest_map="$(mktemp)"
drift_report=""
tmp=""
tmp_new=""
sarif_tmp=""
sarif_report_path=""
if [[ "$check" == "true" ]]; then
  drift_report="$(mktemp)"
else
  tmp="$(mktemp)"
  tmp_new="$(mktemp)"
fi
trap 'rm -f "$digest_map" "$drift_report" "$tmp" "$tmp_new" "$sarif_tmp"' EXIT
while IFS= read -r ref; do
  digest=$(resolve_digest "$ref")
  [[ "$digest" =~ ^${digest_value_re}$ ]] || fatal "Could not resolve a valid digest for $ref"
  printf '%s %s\n' "$ref" "$digest" >>"$digest_map"
  print_kv "$ref" "$digest"
done <<<"$refs"

#------------------------------------------------------------------------------
# Evaluate
#------------------------------------------------------------------------------
section "Evaluating Pins"

affected_file_count=0
if [[ "$check" == "true" ]]; then
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    while IFS=' ' read -r ref digest; do
      ref_re="$(escape_ref_re "$ref")"
      record_drift "$file" "$ref" "$ref_re" "$digest"
    done <"$digest_map"
  done <<<"$files"
  drift_files="$(cut -f1 "$drift_report" | sort -u)"
  if [[ -n "$drift_files" ]]; then
    affected_file_count=$(printf '%s\n' "$drift_files" | grep -c .)
  fi
  while IFS=$'\t' read -r file line_number _ _ ref pinned_digest digest; do
    [[ -n "$file" ]] || continue
    warn "Digest drift detected at $file:$line_number for $ref ($pinned_digest -> $digest)"
  done <"$drift_report"
else
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    cp "$file" "$tmp"
    while IFS=' ' read -r ref digest; do
      # Only '.' is an ERE metacharacter in these refs; '#' is the sed delimiter.
      ref_re="$(escape_ref_re "$ref")"
      # Left-anchor on line start or a non-ref char (\1 re-emits it) so a short ref
      # cannot match inside a longer one (e.g. bar:1 within foo/bar:1).
      sed -E "s#${ref_left_boundary_re}(${ref_re})@${digest_value_re}#\1\2@${digest}#g" "$tmp" >"$tmp_new"
      mv "$tmp_new" "$tmp"
    done <"$digest_map"

    if cmp -s "$file" "$tmp"; then
      continue
    fi
    affected_file_count=$((affected_file_count + 1))
    if [[ "$dry_run" == "true" ]]; then
      info "[dry-run] Would update $file"
      diff -u "$file" "$tmp" || true
    else
      # Overwrite in place (cp keeps the target's own permissions, unlike mv from the 0600 mktemp file).
      cp "$tmp" "$file"
      info "Updated $file"
    fi
  done <<<"$files"
fi

drift_count=0
if [[ "$check" == "true" ]]; then
  drift_count=$(wc -l <"$drift_report" | tr -d '[:space:]')
fi

if [[ -n "$sarif_output" ]]; then
  write_sarif "$sarif_output"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Images Checked" "$ref_count"
if [[ "$check" == "true" ]]; then
  print_kv "Drift Findings" "$drift_count"
  print_kv "Files With Drift" "$affected_file_count"
elif [[ "$dry_run" == "true" ]]; then
  print_kv "Files To Update" "$affected_file_count"
else
  print_kv "Files Changed" "$affected_file_count"
fi
print_kv "Check Mode" "$check"
print_kv "Dry Run" "$dry_run"
[[ -z "$sarif_report_path" ]] || print_kv "SARIF Report" "$sarif_report_path"

if [[ "$check" == "true" && "$drift_count" -gt 0 ]]; then
  exit 2
fi
