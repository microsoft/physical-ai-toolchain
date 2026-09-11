#!/usr/bin/env bash
# Resolve an OCI image digest, scan with Trivy and Grype, and merge findings into
# an OpenVEX document without discarding existing product-specific analysis.
# cspell:ignore urandom
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Default image is a runnable AML base. The fleet inference image now uses
# 'scratch' (no packages, no scannable surface) so this script does not apply
# to it by default. Pass --image explicitly when scanning a runnable variant.
DEFAULT_IMAGE="mcr.microsoft.com/azureml/minimal-py312-inference@sha256:cfb7101d17e0d397f9369639b9873282c9ea386c709c434bb0100745f647c6c0"
DEFAULT_AUTHOR="Physical AI Toolchain Security Team"
DEFAULT_ID_BASE="https://github.com/microsoft/physical-ai-toolchain/security/vex"
DEFAULT_SEVERITY="HIGH,CRITICAL"
DEFAULT_OUTPUT="$REPO_ROOT/security/vex/inference-base.openvex.json"
DEFAULT_SCAN_DIR="$REPO_ROOT/.scan"
OPENVEX_SCHEMA="$SCRIPT_DIR/openvex-0.2.0.schema.json"
OPENVEX_VALIDATOR="$SCRIPT_DIR/validate_openvex.py"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Scan an OCI image and merge findings into an OpenVEX document.

Existing statements and unrelated metadata are preserved. Revision metadata and
scan provenance are updated, and newly discovered CVEs are added as
under_investigation for the resolved digest.

OPTIONS:
    -h, --help               Show this help message
    -i, --image REF          Image reference to resolve and scan
                             (default: $DEFAULT_IMAGE)
    -p, --product NAME       OCI product name in the OpenVEX product purl
                             (default: derived from --image)
    -r, --repo-url URL       repository_url qualifier for the product purl
                             (default: derived from --image)
    -s, --severity LIST      Comma-separated severities to include
                             (default: $DEFAULT_SEVERITY; use ALL for everything)
    -o, --output PATH        Output OpenVEX JSON path
                             (default: $DEFAULT_OUTPUT)
    -d, --scan-dir DIR       Directory for raw scanner output
                             (default: $DEFAULT_SCAN_DIR)
        --author NAME        OpenVEX author string (default: existing document
                             author when merging; otherwise $DEFAULT_AUTHOR)
        --id-base URL        Base URL for the OpenVEX @id (default: $DEFAULT_ID_BASE)
        --skip-scan          Reuse scanner output in --scan-dir only when its
                             metadata matches the resolved digest and severity
        --config-preview     Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --image mcr.microsoft.com/azureml/minimal-py312-inference:1.0
    $(basename "$0") --severity ALL --output security/vex/inference-base.openvex.json
EOF
}

image="$DEFAULT_IMAGE"
product=""
repo_url=""
severity="$DEFAULT_SEVERITY"
output="$DEFAULT_OUTPUT"
scan_dir="$DEFAULT_SCAN_DIR"
author=""
id_base="$DEFAULT_ID_BASE"
skip_scan=false
config_preview=false
output_tmp=""
scan_metadata_tmp=""
scan_work_dir=""
trivy_cache_tmp=""
grype_cache_tmp=""
cve_list_tmp=""
cve_sorted_tmp=""
lock_dir=""
lock_acquired=false
scan_lock_dir=""
scan_lock_acquired=false

cleanup() {
  if [[ -n "$output_tmp" && -f "$output_tmp" ]]; then
    rm -f "$output_tmp"
  fi
  if [[ -n "$scan_metadata_tmp" && -f "$scan_metadata_tmp" ]]; then
    rm -f "$scan_metadata_tmp"
  fi
  if [[ -n "$trivy_cache_tmp" && -f "$trivy_cache_tmp" ]]; then
    rm -f "$trivy_cache_tmp"
  fi
  if [[ -n "$grype_cache_tmp" && -f "$grype_cache_tmp" ]]; then
    rm -f "$grype_cache_tmp"
  fi
  if [[ -n "$cve_list_tmp" && -f "$cve_list_tmp" ]]; then
    rm -f "$cve_list_tmp"
  fi
  if [[ -n "$cve_sorted_tmp" && -f "$cve_sorted_tmp" ]]; then
    rm -f "$cve_sorted_tmp"
  fi
  if [[ "$lock_acquired" == "true" && -n "$lock_dir" && -d "$lock_dir" ]]; then
    rmdir "$lock_dir" 2>/dev/null || true
  fi
  if [[ "$scan_lock_acquired" == "true" && -n "$scan_lock_dir" && -d "$scan_lock_dir" ]]; then
    rmdir "$scan_lock_dir" 2>/dev/null || true
  fi
  if [[ -n "$scan_work_dir" && -d "$scan_work_dir" ]]; then
    rm -f "$scan_work_dir/trivy.json" "$scan_work_dir/grype.json" \
      "$scan_work_dir/cves.txt" "$scan_work_dir/cves.txt.tmp"
    rmdir "$scan_work_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_option_value() {
  [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    -i|--image)        require_option_value "$@"; image="$2"; shift 2 ;;
    -p|--product)      require_option_value "$@"; product="$2"; shift 2 ;;
    -r|--repo-url)     require_option_value "$@"; repo_url="$2"; shift 2 ;;
    -s|--severity)     require_option_value "$@"; severity="$2"; shift 2 ;;
    -o|--output)       require_option_value "$@"; output="$2"; shift 2 ;;
    -d|--scan-dir)     require_option_value "$@"; scan_dir="$2"; shift 2 ;;
    --author)          require_option_value "$@"; author="$2"; shift 2 ;;
    --id-base)         require_option_value "$@"; id_base="$2"; shift 2 ;;
    --skip-scan)       skip_scan=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

IFS=',' read -r -a severity_tokens <<< "$severity"
canonical_severities=()
for token in "${severity_tokens[@]}"; do
  token="${token//[[:space:]]/}"
  token=$(printf '%s' "$token" | tr '[:lower:]' '[:upper:]')
  case "$token" in
    UNKNOWN|LOW|MEDIUM|HIGH|CRITICAL) canonical_severities+=("$token") ;;
    ALL)
      [[ ${#severity_tokens[@]} -eq 1 ]] ||
        fatal "--severity ALL cannot be combined with other values"
      canonical_severities=("ALL")
      ;;
    *) fatal "Invalid severity: ${token:-<empty>}" ;;
  esac
done
severity=$(IFS=,; printf '%s' "${canonical_severities[*]}")

require_tools jq trivy grype uv
# digest resolution: prefer crane, fall back to docker buildx
if command -v crane &>/dev/null; then
  resolve_digest() { crane digest "$1"; }
elif command -v docker &>/dev/null; then
  resolve_digest() {
    docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}'
  }
else
  fatal "Need 'crane' or 'docker' to resolve image digest"
fi

#------------------------------------------------------------------------------
# Resolve digest and assemble identifiers
#------------------------------------------------------------------------------
section "Resolve image digest"

digest=$(resolve_digest "$image") || fatal "Failed to resolve digest for $image"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fatal "Unexpected digest format: $digest"
image_ref="${image%@*}"
# Strip a tag only from the last path segment so registry ports survive.
image_last_segment="${image_ref##*/}"
if [[ "$image_last_segment" == *:* ]]; then
  image_ref="${image_ref%:*}"
fi
product="${product:-${image_ref##*/}}"
if [[ -z "$repo_url" ]]; then
  [[ "$image_ref" == */* ]] ||
    fatal "Cannot derive repository URL from unqualified image '$image'; pass --repo-url"
  repo_url="${image_ref%/*}"
fi
[[ "$product" =~ ^[A-Za-z0-9._~-]+$ ]] ||
  fatal "Invalid OCI product name: $product"
[[ "$repo_url" != *[\?\&\#[:space:]]* ]] ||
  fatal "Invalid repository URL qualifier: $repo_url"
image_ref="${image_ref}@${digest}"
purl="pkg:oci/${product}@${digest}?repository_url=${repo_url}"

print_kv "Image"     "$image"
print_kv "Digest"    "$digest"
print_kv "Product"   "$purl"
print_kv "Output"    "$output"
print_kv "Scan dir"  "$scan_dir"
print_kv "Severity"  "$severity"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi

mkdir -p "$scan_dir" "$(dirname "$output")"
lock_dir="${output}.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  if [[ -d "$lock_dir" ]]; then
    fatal "Another VEX generation is already writing $output; remove $lock_dir if the prior run was interrupted"
  fi
  fatal "Failed to create VEX generation lock: $lock_dir"
fi
lock_acquired=true
scan_lock_dir="$scan_dir/.generate-vex.lock"
if ! mkdir "$scan_lock_dir" 2>/dev/null; then
  if [[ -d "$scan_lock_dir" ]]; then
    fatal "Another VEX generation is using $scan_dir; remove $scan_lock_dir if the prior run was interrupted"
  fi
  fatal "Failed to create scanner output lock: $scan_lock_dir"
fi
scan_lock_acquired=true

#------------------------------------------------------------------------------
# Run scanners
#------------------------------------------------------------------------------
section "Run scanners"

scan_metadata="$scan_dir/metadata.json"
if [[ "$skip_scan" == "true" ]]; then
  trivy_json="$scan_dir/trivy.json"
  grype_json="$scan_dir/grype.json"
  [[ -s "$trivy_json" ]] || fatal "--skip-scan set but $trivy_json missing/empty"
  [[ -s "$grype_json" ]] || fatal "--skip-scan set but $grype_json missing/empty"
  [[ -s "$scan_metadata" ]] || fatal "--skip-scan set but $scan_metadata missing/empty"
  jq -e \
    --arg digest "$digest" \
    --arg severity "$severity" \
    '.digest == $digest and .severity_filter == $severity' \
    "$scan_metadata" >/dev/null ||
    fatal "Cached scanner output does not match digest $digest and severity $severity"
  info "Reusing existing scanner output"
else
  scan_work_dir=$(mktemp -d "$scan_dir/run.XXXXXX") ||
    fatal "Failed to create scanner workspace in $scan_dir"
  trivy_json="$scan_work_dir/trivy.json"
  grype_json="$scan_work_dir/grype.json"
  trivy_args=(image --format json --output "$trivy_json")
  if [[ "$severity" != "ALL" ]]; then
    trivy_args+=(--severity "$severity")
  fi
  trivy_args+=("$image_ref")
  info "trivy ${trivy_args[*]}"
  trivy "${trivy_args[@]}"

  info "grype $image_ref -o json > $grype_json"
  grype "$image_ref" -o json > "$grype_json"

  rm -f "$scan_metadata"
  trivy_cache_tmp=$(mktemp "$scan_dir/trivy.json.tmp.XXXXXX") ||
    fatal "Failed to create Trivy cache file in $scan_dir"
  grype_cache_tmp=$(mktemp "$scan_dir/grype.json.tmp.XXXXXX") ||
    fatal "Failed to create Grype cache file in $scan_dir"
  cp "$trivy_json" "$trivy_cache_tmp"
  cp "$grype_json" "$grype_cache_tmp"
  mv "$trivy_cache_tmp" "$scan_dir/trivy.json"
  trivy_cache_tmp=""
  mv "$grype_cache_tmp" "$scan_dir/grype.json"
  grype_cache_tmp=""
  scan_metadata_tmp=$(mktemp "$scan_dir/metadata.json.tmp.XXXXXX") ||
    fatal "Failed to create scanner metadata in $scan_dir"
  jq -n \
    --arg digest "$digest" \
    --arg severity "$severity" \
    '{digest: $digest, severity_filter: $severity}' > "$scan_metadata_tmp"
  mv "$scan_metadata_tmp" "$scan_metadata"
  scan_metadata_tmp=""
fi

#------------------------------------------------------------------------------
# Aggregate unique CVE list
#------------------------------------------------------------------------------
section "Aggregate findings"

if [[ -n "$scan_work_dir" ]]; then
  cve_list="$scan_work_dir/cves.txt"
else
  cve_list="$scan_dir/cves.txt"
fi

jq -e '
  all(
    .Results[]?.Vulnerabilities[]?;
    (.VulnerabilityID | type == "string" and length > 0)
    and (.Severity | type == "string" and length > 0)
  )
' "$trivy_json" >/dev/null || fatal "Trivy output contains an invalid vulnerability entry"
jq -e '
  all(
    .matches[]?;
    (.vulnerability.id | type == "string" and length > 0)
    and (.vulnerability.severity | type == "string" and length > 0)
  )
' "$grype_json" >/dev/null || fatal "Grype output contains an invalid vulnerability entry"

# Build severity allow-list as a JSON array (empty array == accept all).
if [[ "$severity" == "ALL" ]]; then
  sev_json='[]'
else
  sev_json=$(jq -nc --arg s "$severity" '$s | ascii_upcase | split(",") | map(gsub("^\\s+|\\s+$";""))')
fi

cve_list_tmp=$(mktemp "${cve_list}.tmp.XXXXXX") ||
  fatal "Failed to create temporary CVE list beside $cve_list"
cve_sorted_tmp=$(mktemp "${cve_list}.sorted.XXXXXX") ||
  fatal "Failed to create sorted CVE list beside $cve_list"

jq -r --argjson sev "$sev_json" '
  .Results[]?.Vulnerabilities[]?
  | .Severity as $s
  | select(($sev | length) == 0 or ($sev | index($s)))
  | .VulnerabilityID
' "$trivy_json" > "$cve_list_tmp"

jq -r --argjson sev "$sev_json" '
  .matches[]?
  | (.vulnerability.severity | ascii_upcase) as $s
  | select(($sev | length) == 0 or ($sev | index($s)))
  | .vulnerability.id
' "$grype_json" >> "$cve_list_tmp"

sort -u "$cve_list_tmp" > "$cve_sorted_tmp"
mv "$cve_sorted_tmp" "$cve_list"
cve_sorted_tmp=""
rm -f "$cve_list_tmp"
cve_list_tmp=""
if [[ -n "$scan_work_dir" ]]; then
  cp "$cve_list" "$scan_dir/cves.txt"
fi
cve_count=$(wc -l < "$cve_list" | tr -d ' ')
print_kv "Unique CVEs" "$cve_count"

if [[ "$cve_count" -eq 0 && ! -e "$output" ]]; then
  section "Deployment Summary"
  print_kv "Image"         "$image_ref"
  print_kv "OpenVEX file"  "<not created>"
  print_kv "Unique CVEs"   "$cve_count"
  info "No vulnerabilities found; OpenVEX v0.2.0 requires at least one statement."
  exit 0
fi

#------------------------------------------------------------------------------
# Merge OpenVEX document
#------------------------------------------------------------------------------
section "Merge OpenVEX document"

validate_vex_document() {
  uv run --frozen --no-sync python "$OPENVEX_VALIDATOR" --schema "$OPENVEX_SCHEMA" "$1" &&
    jq -ce . "$1"
}

if [[ -f "$output" ]]; then
  previous_document=$(validate_vex_document "$output") ||
    fatal "Existing OpenVEX document is invalid: $output"

  prev_version=$(jq -r '.version | floor' <<< "$previous_document")
  prev_timestamp=$(jq -r '.timestamp' <<< "$previous_document")
  existing_author=$(jq -r '.author // empty' <<< "$previous_document")
  existing_statements=$(jq -c --arg timestamp "$prev_timestamp" '
    [
      .statements[]?
      | if (.timestamp | type == "string" and length > 0)
        then .
        else . + {timestamp: $timestamp}
        end
    ]
  ' <<< "$previous_document")
else
  previous_document='{}'
  prev_version=0
  existing_author=""
  existing_statements='[]'
fi

effective_author="${author:-${existing_author:-$DEFAULT_AUTHOR}}"
next_version=$((prev_version + 1))
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
date_slug="${timestamp%%T*}"
revision_nonce=$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')
vex_id="${id_base}/${product}/${date_slug}-v${next_version}-${revision_nonce}"

# New statements remain under_investigation until evidence supports another
# status. Existing statements retain their status and effective timestamp.
script_rel="scripts/security/$(basename "$0")"
tooling="$script_rel using Trivy and Grype scanner evidence; human-reviewed; published with Sigstore"
output_tmp=$(mktemp "${output}.tmp.XXXXXX") ||
  fatal "Failed to create temporary output beside $output"

jq -n \
  --arg id "$vex_id" \
  --arg author "$effective_author" \
  --arg ts "$timestamp" \
  --argjson version "$next_version" \
  --arg purl "$purl" \
  --arg tooling "$tooling" \
  --argjson previous "$previous_document" \
  --argjson existing "$existing_statements" \
  --rawfile cves "$cve_list" \
  '(
    $cves
    | split("\n")
    | map(select(length > 0))
    | map({
        vulnerability: { name: . },
        products: [ { "@id": $purl } ],
        status: "under_investigation"
      })
  ) as $generated
  | ([
      $existing[]
      | select(any(.products[]?; .["@id"] == $purl))
      | .vulnerability.name
    ]) as $seen
  | ($previous + {
      "@context": "https://openvex.dev/ns/v0.2.0",
      "@id": $id,
      "author": $author,
      "timestamp": $ts,
      "version": $version,
      "tooling": $tooling,
      "statements": (
        $existing + (
          $generated
          | map(select(.vulnerability.name as $name | $seen | index($name) | not))
        )
      )
    })
  | if ($previous | has("last_updated"))
    then . + {"last_updated": $ts}
    else .
    end' > "$output_tmp"

validate_vex_document "$output_tmp" >/dev/null ||
  fatal "Generated OpenVEX document is invalid"
final_statement_count=$(jq -er '.statements | length' "$output_tmp")
existing_statement_count=$(jq -r 'length' <<< "$existing_statements")
new_statement_count=$((final_statement_count - existing_statement_count))
mv "$output_tmp" "$output"
output_tmp=""

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Image"         "$image_ref"
print_kv "OpenVEX file"  "$output"
print_kv "Unique CVEs"   "$cve_count"
print_kv "Statements new" "$new_statement_count"
print_kv "Statements"    "$final_statement_count"
print_kv "Version"       "$next_version"
info "Triage each statement using product-specific evidence before attaching with cosign."
info "Retain under_investigation when the evidence does not support another status."
