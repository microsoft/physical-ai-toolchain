#!/usr/bin/env bash
# Resolve an OCI image digest, scan with Trivy and Grype, and emit a stub
# OpenVEX document with one `under_investigation` statement per discovered CVE.
# Operators triage each statement and replace its status with not_affected /
# affected / fixed before publishing.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Default image is a runnable AML base. The fleet inference image now uses
# 'scratch' (no packages, no scannable surface) so this script does not apply
# to it by default — see security/vex/inference-base.openvex.json. Pass --image
# explicitly when scanning a runnable variant.
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

Scan an OCI image and emit a stub OpenVEX document for triage.

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
image_ref="${image_ref%:*}@${digest}"
purl="pkg:oci/${product}@${digest}?repository_url=${repo_url}"
timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
date_slug="$(date -u +%Y-%m-%d)"
vex_id="${id_base}/${product}/${date_slug}"

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
# Emit stub OpenVEX document
#------------------------------------------------------------------------------
section "Write OpenVEX stub"

if [[ -f "$output" ]]; then
  prev_version=$(jq -r '.version // 0' "$output" 2>/dev/null || echo 0)
else
  prev_version=0
fi
next_version=$((prev_version + 1))

# All statements emitted as under_investigation; operators must triage each
# CVE and replace status (and add justification/action_statement) before
# publishing. See https://openvex.dev/ns/v0.2.0 for the schema.
script_rel="scripts/security/$(basename "$0")"
generator="$script_rel --image $image_ref --severity $severity"

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
  --rawfile cves "$cve_list" \
  '{
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
      $cves
      | split("\n")
      | map(select(length > 0))
      | map({
          vulnerability: { name: . },
          products: [ { "@id": $purl } ],
          status: "under_investigation"
        })
    )
  }' > "$output"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Image"        "$image_ref"
print_kv "OpenVEX file" "$output"
print_kv "Statements"   "$cve_count"
print_kv "Version"      "$next_version"
info "Triage each statement: set status to not_affected / affected / fixed and"
info "add justification or action_statement before attaching with cosign."
