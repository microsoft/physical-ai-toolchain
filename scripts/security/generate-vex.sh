#!/usr/bin/env bash
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
DEFAULT_PRODUCT="minimal-py312-inference"
DEFAULT_REPO_URL="mcr.microsoft.com/azureml"
DEFAULT_AUTHOR="Physical AI Toolchain Security Team"
DEFAULT_ID_BASE="https://github.com/microsoft/physical-ai-toolchain/security/vex"
DEFAULT_SEVERITY="HIGH,CRITICAL"
DEFAULT_OUTPUT="$REPO_ROOT/security/vex/inference-base.openvex.json"
DEFAULT_SCAN_DIR="$REPO_ROOT/.scan"

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
                             (default: $DEFAULT_PRODUCT)
    -r, --repo-url URL       repository_url qualifier for the product purl
                             (default: $DEFAULT_REPO_URL)
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
product="$DEFAULT_PRODUCT"
repo_url="$DEFAULT_REPO_URL"
severity="$DEFAULT_SEVERITY"
output="$DEFAULT_OUTPUT"
scan_dir="$DEFAULT_SCAN_DIR"
author="$DEFAULT_AUTHOR"
id_base="$DEFAULT_ID_BASE"
skip_scan=false
config_preview=false
output_tmp=""
lock_dir=""
lock_acquired=false

cleanup() {
  if [[ -n "$output_tmp" && -f "$output_tmp" ]]; then
    rm -f "$output_tmp"
  fi
  if [[ "$lock_acquired" == "true" && -n "$lock_dir" && -d "$lock_dir" ]]; then
    rmdir "$lock_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT

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
image_last_segment="${image_ref##*/}"
if [[ "$image_last_segment" == *:* ]]; then
  image_ref="${image_ref%:*}"
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

#------------------------------------------------------------------------------
# Run scanners
#------------------------------------------------------------------------------
section "Run scanners"

trivy_json="$scan_dir/trivy.json"
grype_json="$scan_dir/grype.json"

if [[ "$skip_scan" == "true" ]]; then
  [[ -s "$trivy_json" ]] || fatal "--skip-scan set but $trivy_json missing/empty"
  [[ -s "$grype_json" ]] || fatal "--skip-scan set but $grype_json missing/empty"
  info "Reusing existing scanner output"
else
  trivy_args=(image --format json --output "$trivy_json")
  if [[ "$severity" != "ALL" ]]; then
    trivy_args+=(--severity "$severity")
  fi
  trivy_args+=("$image_ref")
  info "trivy ${trivy_args[*]}"
  trivy "${trivy_args[@]}"

  info "grype $image_ref -o json > $grype_json"
  grype "$image_ref" -o json > "$grype_json"
fi

#------------------------------------------------------------------------------
# Aggregate unique CVE list
#------------------------------------------------------------------------------
section "Aggregate findings"

cve_list="$scan_dir/cves.txt"

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
  .matches[]
  | (.vulnerability.severity | ascii_upcase) as $s
  | select(($sev | length) == 0 or ($sev | index($s)))
  | .vulnerability.id
' "$grype_json" >> "$cve_list.tmp"

sort -u "$cve_list.tmp" > "$cve_list"
rm -f "$cve_list.tmp"
cve_count=$(wc -l < "$cve_list" | tr -d ' ')
print_kv "Unique CVEs" "$cve_count"

#------------------------------------------------------------------------------
# Merge OpenVEX document
#------------------------------------------------------------------------------
section "Merge OpenVEX document"

output_dir="$(dirname "$output")"
mkdir -p "$output_dir"
lock_dir="${output}.lock"
mkdir "$lock_dir" 2>/dev/null ||
  fatal "Another VEX generation is already writing $output"
lock_acquired=true

if [[ -f "$output" ]]; then
  previous_document=$(jq -ce '
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

    if (.version | type) != "number"
      or .version < 1
      or .version != (.version | floor)
    then error("version must be a positive integer")
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
    else .
    end
  ' "$output") ||
    fatal "Existing OpenVEX document is invalid: $output"

  duplicate_pairs=$(jq -c '
    [
      .statements[]? as $statement
      | ($statement.vulnerability.name // $statement.vulnerability["@id"]) as $vulnerability
      | $statement.products[]?
      | [$vulnerability, .["@id"]]
    ]
    | group_by(.)
    | map(select(length > 1) | .[0])
  ' <<< "$previous_document")
  [[ "$duplicate_pairs" == "[]" ]] ||
    fatal "Existing OpenVEX document has duplicate vulnerability/product pairs: $duplicate_pairs"

  prev_version=$(jq -r '.version' <<< "$previous_document")
  prev_timestamp=$(jq -r '.timestamp' <<< "$previous_document")
  existing_statements=$(jq -c --arg timestamp "$prev_timestamp" '
    [
      .statements[]?
      | if has("timestamp") then . else . + {timestamp: $timestamp} end
    ]
  ' <<< "$previous_document")
else
  previous_document='{}'
  prev_version=0
  existing_statements='[]'
fi

next_version=$((10#$prev_version + 1))
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
date_slug="${timestamp%%T*}"
revision_nonce=$(printf '%05d%05d' "$RANDOM" "$RANDOM")
vex_id="${id_base}/${product}/${date_slug}-v${next_version}-${revision_nonce}"

# New statements remain under_investigation until evidence supports another
# status. Existing statements retain their status and effective timestamp.
script_rel="scripts/security/$(basename "$0")"
generator="$script_rel --image $image_ref --severity $severity"
output_tmp=$(mktemp "${output}.tmp.XXXXXX") ||
  fatal "Failed to create temporary output beside $output"

jq -n \
  --arg id "$vex_id" \
  --arg author "$author" \
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
      | (.vulnerability.name // .vulnerability["@id"])
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

final_statement_count=$(jq -er '.statements | length' "$output_tmp") ||
  fatal "Generated OpenVEX document is invalid"
existing_statement_count=$(jq -nr --argjson existing "$existing_statements" '$existing | length')
new_statement_count=$((final_statement_count - existing_statement_count))
mv "$output_tmp" "$output"
output_tmp=""

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Image"         "$image_ref"
print_kv "OpenVEX file"  "$output"
print_kv "Scanned CVEs"  "$cve_count"
print_kv "New statements" "$new_statement_count"
print_kv "Statements"    "$final_statement_count"
print_kv "Version"       "$next_version"
info "Triage each statement using product-specific evidence before attaching with cosign."
info "Retain under_investigation when the evidence does not support another status."
