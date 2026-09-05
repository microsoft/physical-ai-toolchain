#!/usr/bin/env bash
# cspell:ignore redir unmatch
# Refresh @sha256 digest pins for OCI container images referenced in tracked files.
#
# References are discovered automatically: every "<image>:<tag>@sha256:<digest>" is
# re-resolved to its current registry digest and rewritten in place. Dockerfiles and
# compose files are owned by Dependabot; gh-aw compiled workflows are compiler-owned.
# Test fixtures and Pester test files are also excluded. AzureML environment versions
# derived from shared image defaults are synchronized in the same run.
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
AzureML environment versions derived from checked-in image defaults are synchronized.
Check mode is a CI signal that supports SARIF and exits 2 on drift.
Dry-run mode is a local preview of the changes that write mode would apply.
Anonymous OCI registries are supported. Anonymous pull tokens are acquired only for
Docker Hub and NGC; other registries that require authentication are not supported.

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

# Reference shape: [registry[:port]/]repo[:path]:tag@sha256:<64 hex>. Pathspecs
# exclude files whose digests are maintained by other tooling (Dependabot, gh-aw).
digest_value_re='sha256:[0-9a-f]{64}'
ref_left_boundary_re='(^|[^A-Za-z0-9._/:-])'
registry_prefix_re='([A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]+)?/)'
repository_re='[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*'
digest_ref_re="(${registry_prefix_re})?${repository_re}:[A-Za-z0-9._-]+@${digest_value_re}"
exclude_paths=(
  ':(exclude,glob).github/workflows/*.lock.yml'
  ':(exclude,glob)**/Dockerfile*'
  ':(exclude,glob)**/*compose*.y*ml'
  ':(exclude,glob)**/tests/Fixtures/**'
  ':(exclude,glob)**/*.Tests.ps1'
)
azureml_pin_specs=(
  'DEFAULT_ISAAC_LAB_IMAGE|isaaclab-training-env|training/rl/workflows/azureml/train.yaml'
  'DEFAULT_ISAAC_LAB_IMAGE|isaaclab-training-env|evaluation/sil/workflows/azureml/isaaclab-evaluation.yaml'
  'DEFAULT_LEROBOT_TRAIN_IMAGE|lerobot-training-env|training/il/workflows/azureml/lerobot-train.yaml'
  'DEFAULT_LEROBOT_EVAL_IMAGE|lerobot-inference-env|evaluation/sil/workflows/azureml/lerobot-eval.yaml'
  'DEFAULT_LEROBOT_TRAIN_IMAGE|vla-pi0-training-env|training/vla/workflows/azureml/vla-pi0-train.yaml'
)
azureml_environment_line_re="^[[:space:]]*(-[[:space:]]*)?[\"']?environment[\"']?[[:space:]]*:[[:space:]]*[\"']?azureml:"

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
  *.*/* | *:*/* | localhost/*)
    host="${ref%%/*}"
    repo="${ref#*/}"
    ;;
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
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
          "container-image-digest-drift" "$file" "$line_number" "$start_column" "$end_column" \
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
        rule: .[0],
        file: .[1],
        line: (.[2] | tonumber),
        startColumn: (.[3] | tonumber),
        endColumn: (.[4] | tonumber),
        ref: .[5],
        pinned: .[6],
        resolved: .[7]
      }] as $findings
      | {
          version: "2.1.0",
          "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
          runs: [{
            tool: {
              driver: {
                name: "Container Image Digest Freshness",
                informationUri: "https://github.com/microsoft/physical-ai-toolchain",
                rules: [
                  {
                    id: "container-image-digest-drift",
                    shortDescription: {
                      text: "Pinned container image digest differs from the tag digest"
                    },
                    helpUri: "https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-image-digests.sh",
                    defaultConfiguration: { level: "warning" }
                  },
                  {
                    id: "azureml-environment-version-drift",
                    shortDescription: {
                      text: "Azure ML environment version differs from the image-derived version"
                    },
                    helpUri: "https://github.com/microsoft/physical-ai-toolchain/blob/main/scripts/update-image-digests.sh",
                    defaultConfiguration: { level: "warning" }
                  }
                ]
              }
            },
            results: [
              $findings[] | {
                ruleId: .rule,
                level: "warning",
                message: {
                  text: (
                    if .rule == "container-image-digest-drift" then
                      "Pinned digest " + .pinned + " for " + .ref
                        + " differs from the registry digest " + .resolved + "."
                    else
                      "Pinned Azure ML environment version " + .pinned + " for " + .ref
                        + " differs from the image-derived version " + .resolved + "."
                    end
                  )
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

read_checked_in_image_default() {
  local variable="$1" lines line_count line prefix suffix image
  lines=$(grep -E "^${variable}=" "$REPO_ROOT/scripts/lib/common.sh" || true)
  [[ -n "$lines" ]] || fatal "No checked-in default for $variable in scripts/lib/common.sh"
  line_count=$(printf '%s\n' "$lines" | grep -c .)
  [[ "$line_count" -eq 1 ]] || fatal "Expected one checked-in default for $variable in scripts/lib/common.sh, found $line_count"
  line="$lines"
  prefix="${variable}=\"\${${variable}:-"
  suffix='}"'
  [[ "$line" == "$prefix"*"$suffix" ]] || fatal "Could not parse checked-in default for $variable"
  image="${line#"$prefix"}"
  printf '%s\n' "${image%"$suffix"}"
}

apply_file_update() {
  local file="$1" candidate="$2"
  if cmp -s "$file" "$candidate"; then
    return 1
  fi

  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] Would update $file"
    diff -u "$file" "$candidate" || true
  else
    # Overwrite in place so the target retains its permissions.
    cp "$candidate" "$file"
    info "Updated $file"
  fi
  return 0
}

#------------------------------------------------------------------------------
# Discover
#------------------------------------------------------------------------------
section "Discovering Pins"

refs=$(git grep -hoE "$digest_ref_re" -- "${exclude_paths[@]}" 2>/dev/null |
  sed -E 's/@sha256:[0-9a-f]{64}//' | sort -u || true)
files=$(git grep -lE "$digest_ref_re" -- "${exclude_paths[@]}" 2>/dev/null || true)
azureml_files=""
for spec in "${azureml_pin_specs[@]}"; do
  spec_file="${spec##*|}"
  if [[ -f "$spec_file" ]]; then
    azureml_files=$(printf '%s\n%s\n' "$azureml_files" "$spec_file" | grep -v '^$' | sort -u)
  fi
done
if [[ -n "$azureml_files" ]]; then
  expected_azureml_files=$(printf '%s\n' "${azureml_pin_specs[@]}" | cut -d'|' -f3 | sort -u)
  missing_expected_azureml_files=$(comm -23 \
    <(printf '%s\n' "$expected_azureml_files" | grep -v '^$' | sort -u) \
    <(printf '%s\n' "$azureml_files" | grep -v '^$' | sort -u))
  [[ -z "$missing_expected_azureml_files" ]] \
    || fatal "AzureML environment pin targets not found: $(printf '%s' "$missing_expected_azureml_files" | tr '\n' ' ')"
  discovered_azureml_files=$(git grep --untracked -lE "$azureml_environment_line_re" -- \
    ':(glob)**/workflows/**/*.yaml' ':(glob)**/workflows/**/*.yml' "${exclude_paths[@]}" 2>/dev/null || true)
  missing_azureml_files=$(comm -23 \
    <(printf '%s\n' "$discovered_azureml_files" | grep -v '^$' | sort -u) \
    <(printf '%s\n' "$azureml_files" | grep -v '^$' | sort -u))
  [[ -z "$missing_azureml_files" ]] \
    || fatal "AzureML environment pin targets missing from azureml_pin_specs: $(printf '%s' "$missing_azureml_files" | tr '\n' ' ')"
fi
files_in_scope=$(printf '%s\n%s\n' "$files" "$azureml_files" | grep -v '^$' | sort -u)
[[ -n "$refs" ]] || fatal "No digest-pinned image references found under $REPO_ROOT"

ref_count=$(printf '%s\n' "$refs" | grep -c .)
file_count=$(printf '%s\n' "$files_in_scope" | grep -c .)
print_kv "Images Discovered" "$ref_count"
print_kv "Files Discovered" "$file_count"

if [[ "$config_preview" == "true" ]]; then
  section "Discovered Images"
  while IFS= read -r ref; do print_kv "Image" "$ref"; done <<<"$refs"
  section "Discovered Files"
  while IFS= read -r file; do print_kv "File" "$file"; done <<<"$files_in_scope"
  exit 0
fi

#------------------------------------------------------------------------------
# Resolve
#------------------------------------------------------------------------------
section "Resolving Digests"

digest_map="$(mktemp)"
azureml_update_map="$(mktemp)"
drift_report=""
affected_files_report="$(mktemp)"
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
trap 'rm -f "$digest_map" "$azureml_update_map" "$affected_files_report" "$drift_report" "$tmp" "$tmp_new" "$sarif_tmp"' EXIT
while IFS= read -r ref; do
  digest=$(resolve_digest "$ref")
  [[ "$digest" =~ ^${digest_value_re}$ ]] || fatal "Could not resolve a valid digest for $ref"
  printf '%s %s\n' "$ref" "$digest" >>"$digest_map"
  print_kv "$ref" "$digest"
done <<<"$refs"

if [[ -n "$azureml_files" ]]; then
  # Validate every AzureML synchronization target before writing any file.
  while IFS='|' read -r variable environment_name file; do
    image=$(read_checked_in_image_default "$variable")
    ref="${image%@*}"
    digest=$(awk -v ref="$ref" '$1 == ref { print $2; exit }' "$digest_map")
    [[ "$digest" == sha256:* ]] || fatal "No resolved digest found for $variable ($ref)"
    version=$(derive_azureml_environment_version_from_image "${ref}@${digest}")
    replacement="environment: azureml:${environment_name}:${version}"
    grep -qE "^[[:space:]]*(-[[:space:]]*)?[\"']?environment[\"']?[[:space:]]*:[[:space:]]*[\"']?azureml:${environment_name}:[A-Za-z0-9._-]+[\"']?[[:space:]]*$" "$file" \
      || fatal "Could not find AzureML environment pin in $file"
    printf '%s\t%s\t%s\n' "$environment_name" "$file" "$replacement" >>"$azureml_update_map"
  done < <(printf '%s\n' "${azureml_pin_specs[@]}")
fi

#------------------------------------------------------------------------------
# Evaluate
#------------------------------------------------------------------------------
section "Evaluating Pins"

azureml_updated=0
affected_file_count=0
if [[ "$check" == "true" ]]; then
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    while IFS=' ' read -r ref digest; do
      ref_re="$(escape_ref_re "$ref")"
      record_drift "$file" "$ref" "$ref_re" "$digest"
    done <"$digest_map"
  done <<<"$files"
  cut -f2 "$drift_report" | sort -u >>"$affected_files_report"
  while IFS=$'\t' read -r _ file line_number _ _ ref pinned_digest digest; do
    [[ -n "$file" ]] || continue
    warn "Digest drift detected at $file:$line_number for $ref ($pinned_digest -> $digest)"
  done <"$drift_report"

  while IFS=$'\t' read -r environment_name environment_file replacement; do
    [[ -n "$environment_file" ]] || continue
    environment_ref="${replacement#environment: }"
    if ! grep -qE "^[[:space:]]*(-[[:space:]]*)?[\"']?environment[\"']?[[:space:]]*:[[:space:]]*[\"']?${environment_ref}[\"']?[[:space:]]*$" "$environment_file"; then
      environment_match=$(grep -nE "^[[:space:]]*(-[[:space:]]*)?[\"']?environment[\"']?[[:space:]]*:[[:space:]]*[\"']?azureml:${environment_name}:[A-Za-z0-9._-]+[\"']?[[:space:]]*$" "$environment_file")
      line_number="${environment_match%%:*}"
      line_text="${environment_match#*:}"
      pinned_ref=$(printf '%s\n' "$line_text" | grep -oE "azureml:${environment_name}:[A-Za-z0-9._-]+")
      pinned_version="${pinned_ref##*:}"
      resolved_version="${environment_ref##*:}"
      azureml_updated=$((azureml_updated + 1))
      printf '%s\n' "$environment_file" >>"$affected_files_report"
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "azureml-environment-version-drift" "$environment_file" "$line_number" "1" "$(( ${#line_text} + 1 ))" \
        "azureml:${environment_name}" "$pinned_version" "$resolved_version" >>"$drift_report"
      warn "AzureML environment drift detected in $environment_file for $environment_name"
    fi
  done <"$azureml_update_map"
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

    while IFS=$'\t' read -r environment_name environment_file replacement; do
      [[ "$environment_file" == "$file" ]] || continue
      environment_ref="${replacement#environment: }"
      sed -E "s#^([[:space:]]*(-[[:space:]]*)?[\"']?environment[\"']?[[:space:]]*:[[:space:]]*)[\"']?azureml:${environment_name}:[A-Za-z0-9._-]+[\"']?[[:space:]]*\$#\1${environment_ref}#" "$tmp" >"$tmp_new"
      if ! cmp -s "$tmp" "$tmp_new"; then
        azureml_updated=$((azureml_updated + 1))
      fi
      mv "$tmp_new" "$tmp"
    done <"$azureml_update_map"

    if apply_file_update "$file" "$tmp"; then
      affected_file_count=$((affected_file_count + 1))
    fi
  done <<<"$files_in_scope"
fi

drift_count=0
if [[ "$check" == "true" ]]; then
  drift_count=$(wc -l <"$drift_report" | tr -d '[:space:]')
  sort -u "$affected_files_report" -o "$affected_files_report"
  affected_file_count=$(grep -c . "$affected_files_report" || true)
fi

if [[ -n "$sarif_output" ]]; then
  write_sarif "$sarif_output"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Images Checked" "$ref_count"
print_kv "Environment References Changed" "$azureml_updated"
if [[ "$check" == "true" ]]; then
  print_kv "Drift Findings" "$drift_count"
  print_kv "Files With Drift" "$affected_file_count"
elif [[ "$dry_run" == "true" ]]; then
  print_kv "Files To Update" "$affected_file_count"
  print_kv "Files Updated" "$affected_file_count"
else
  print_kv "Files Changed" "$affected_file_count"
  print_kv "Files Updated" "$affected_file_count"
fi
print_kv "Check Mode" "$check"
print_kv "Dry Run" "$dry_run"
[[ -z "$sarif_report_path" ]] || print_kv "SARIF Report" "$sarif_report_path"

if [[ "$azureml_updated" -gt 0 && "$check" != "true" ]]; then
  if [[ "$dry_run" == "true" ]]; then
    warn "[dry-run] Changed AzureML environment versions would require registration before direct template submission"
  else
    warn "Register changed AzureML environment versions before direct template submission"
  fi
fi

if [[ "$check" == "true" && "$drift_count" -gt 0 ]]; then
  exit 2
fi
