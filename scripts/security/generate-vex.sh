#!/usr/bin/env bash
# cspell:ignore urandom
# Resolve an OCI image digest, scan with Trivy and Grype, and merge findings into
# an OpenVEX document without discarding existing product-specific analysis.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Default image is a runnable AML base. The fleet inference image now uses
# 'scratch' (no packages, no scannable surface) so this script does not apply
# to it by default. Pass --image explicitly when scanning a runnable variant.
DEFAULT_IMAGE="mcr.microsoft.com/azureml/minimal-py312-inference@sha256:cfb7101d17e0d397f9369639b9873282c9ea386c709c434bb0100745f647c6c0"
DEFAULT_REPO_URL="mcr.microsoft.com/azureml"
DEFAULT_AUTHOR="Physical AI Toolchain Security Team"
DEFAULT_ID_BASE="https://github.com/microsoft/physical-ai-toolchain/security/vex"
DEFAULT_SEVERITY="HIGH,CRITICAL"
DEFAULT_OUTPUT="$REPO_ROOT/security/vex/inference-base.openvex.json"
DEFAULT_SCAN_DIR="$REPO_ROOT/.scan"
MAX_VERSION=9007199254740990

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
        --author NAME        OpenVEX author string (default: $DEFAULT_AUTHOR)
        --id-base URL        Base URL for the OpenVEX @id (default: $DEFAULT_ID_BASE)
        --skip-scan          Reuse existing trivy.json/grype.json in --scan-dir
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
scan_work_dir=""
lock_dir=""
lock_acquired=false

cleanup() {
  if [[ -n "$output_tmp" && -f "$output_tmp" ]]; then
    rm -f "$output_tmp"
  fi
  if [[ "$lock_acquired" == "true" && -n "$lock_dir" && -d "$lock_dir" ]]; then
    rmdir "$lock_dir" 2>/dev/null || true
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

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    -i|--image)        image="$2"; shift 2 ;;
    -p|--product)      product="$2"; shift 2 ;;
    -r|--repo-url)     repo_url="$2"; shift 2 ;;
    -s|--severity)     severity="$2"; shift 2 ;;
    -o|--output)       output="$2"; shift 2 ;;
    -d|--scan-dir)     scan_dir="$2"; shift 2 ;;
    --author)          author="$2"; shift 2 ;;
    --id-base)         id_base="$2"; shift 2 ;;
    --skip-scan)       skip_scan=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools jq trivy grype
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
[[ "$digest" == sha256:* ]] || fatal "Unexpected digest format: $digest"
image_ref="${image%@*}"
# Strip a tag only from the last path segment so registry ports survive.
image_last_segment="${image_ref##*/}"
if [[ "$image_last_segment" == *:* ]]; then
  image_ref="${image_ref%:*}"
fi
product="${product:-${image_ref##*/}}"
if [[ -z "$repo_url" ]]; then
  if [[ "$image_ref" == */* ]]; then
    repo_url="${image_ref%/*}"
  else
    repo_url="$DEFAULT_REPO_URL"
  fi
fi
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

#------------------------------------------------------------------------------
# Run scanners
#------------------------------------------------------------------------------
section "Run scanners"

if [[ "$skip_scan" == "true" ]]; then
  trivy_json="$scan_dir/trivy.json"
  grype_json="$scan_dir/grype.json"
  [[ -s "$trivy_json" ]] || fatal "--skip-scan set but $trivy_json missing/empty"
  [[ -s "$grype_json" ]] || fatal "--skip-scan set but $grype_json missing/empty"
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
  cp "$trivy_json" "$scan_dir/trivy.json"
  cp "$grype_json" "$scan_dir/grype.json"
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

# Build severity allow-list as a JSON array (empty array == accept all).
if [[ "$severity" == "ALL" ]]; then
  sev_json='[]'
else
  sev_json=$(jq -nc --arg s "$severity" '$s | ascii_upcase | split(",") | map(gsub("^\\s+|\\s+$";""))')
fi

jq -r --argjson sev "$sev_json" '
  .Results[]?.Vulnerabilities[]?
  | .Severity as $s
  | select(($sev | length) == 0 or ($sev | index($s)))
  | .VulnerabilityID
' "$trivy_json" > "$cve_list.tmp"

jq -r --argjson sev "$sev_json" '
  .matches[]?
  | (.vulnerability.severity | ascii_upcase) as $s
  | select(($sev | length) == 0 or ($sev | index($s)))
  | .vulnerability.id
' "$grype_json" >> "$cve_list.tmp"

sort -u "$cve_list.tmp" > "$cve_list"
rm -f "$cve_list.tmp"
if [[ -n "$scan_work_dir" ]]; then
  cp "$cve_list" "$scan_dir/cves.txt"
fi
cve_count=$(wc -l < "$cve_list" | tr -d ' ')
print_kv "Unique CVEs" "$cve_count"

#------------------------------------------------------------------------------
# Merge OpenVEX document
#------------------------------------------------------------------------------
section "Merge OpenVEX document"

validate_vex_document() {
  jq -ce --argjson max_version "$MAX_VERSION" '
    def non_empty_string:
      type == "string" and length > 0;
    def allowed_status:
      . == "under_investigation"
      or . == "not_affected"
      or . == "affected"
      or . == "fixed";
    def allowed_justification:
      . == "component_not_present"
      or . == "vulnerable_code_not_present"
      or . == "vulnerable_code_not_in_execute_path"
      or . == "vulnerable_code_cannot_be_controlled_by_adversary"
      or . == "inline_mitigations_already_exist";
    def digest_purl:
      type == "string"
      and test("^pkg:oci/.+@sha256:[0-9a-f]{64}\\?.*repository_url=[^&]+");
    def duplicate_pairs:
      [
        .statements[]? as $statement
        | $statement.products[]?
        | [$statement.vulnerability.name, .["@id"]]
      ]
      | group_by(.)
      | map(select(length > 1) | .[0]);

    if (.version | type) != "number"
      or .version < 1
      or .version > $max_version
      or .version != (.version | floor)
    then error("version must be an incrementable positive integer")
    elif (.timestamp | non_empty_string | not)
    then error("timestamp must be a non-empty string")
    elif ((.statements // []) | type) != "array"
    then error("statements must be an array")
    elif any(.statements[]?; (.vulnerability.name | non_empty_string | not))
    then error("every statement must identify its vulnerability by name")
    elif any(.statements[]?; (.products | type) != "array" or (.products | length) == 0)
    then error("every statement must identify at least one product")
    elif any(.statements[]?.products[]?; (.["@id"] | digest_purl | not))
    then error("every product must use a digest-pinned OCI package URL")
    elif any(.statements[]?; (.status | allowed_status | not))
    then error("every statement must use an allowed status")
    elif any(
      .statements[]?;
      .status == "not_affected"
      and (
        (.justification | allowed_justification | not)
        or (.status_notes | non_empty_string | not)
      )
    )
    then error("not_affected statements require an allowed justification and status_notes")
    elif any(
      .statements[]?;
      .status == "affected"
      and (
        (.action_statement | non_empty_string | not)
        or (.status_notes | non_empty_string | not)
      )
    )
    then error("affected statements require action_statement and status_notes")
    elif any(
      .statements[]?;
      .status == "fixed"
      and (.status_notes | non_empty_string | not)
    )
    then error("fixed statements require status_notes")
    elif (duplicate_pairs | length) > 0
    then error("duplicate vulnerability/product pairs: \(duplicate_pairs)")
    else .
    end
  ' "$1"
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
generator="$script_rel --image $image_ref --severity $severity"
output_tmp=$(mktemp "${output}.tmp.XXXXXX") ||
  fatal "Failed to create temporary output beside $output"

jq -n \
  --arg id "$vex_id" \
  --arg author "$effective_author" \
  --arg ts "$timestamp" \
  --argjson version "$next_version" \
  --arg purl "$purl" \
  --arg image "$image" \
  --arg image_ref "$image_ref" \
  --arg digest "$digest" \
  --arg product "$product" \
  --arg severity "$severity" \
  --arg generator "$generator" \
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
      "_source": {
        "image": $image,
        "image_ref": $image_ref,
        "digest": $digest,
        "product": $product,
        "severity_filter": $severity,
        "generator": $generator
      },
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
print_kv "CVEs scanned" "$cve_count"
print_kv "Statements added" "$new_statement_count"
print_kv "Statements total" "$final_statement_count"
print_kv "Version"       "$next_version"
info "Triage each statement using product-specific evidence before attaching with cosign."
info "Retain under_investigation when the evidence does not support another status."
