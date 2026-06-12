#!/usr/bin/env bash
#
# scan-image-vulns.sh - Scan a container image with trivy and filter results
# through a VEX directory using vexctl, emitting either a human-readable table
# or SARIF.

set -o errexit
set -o nounset
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

show_help() {
    cat <<'EOF'
Usage: scan-image-vulns.sh --image <ref> [options]

Scan a container image and filter results through VEX statements.

Required:
  --image <ref>           Image reference

Options:
  --vex-dir <path>        Directory of VEX documents (default: security/vex)
  --format <table|sarif>  Output format (default: table)
  --severity <list>       Comma-separated trivy severities (default: HIGH,CRITICAL)
  --config-preview        Print resolved configuration and exit without scanning
  --help                  Show this help and exit
EOF
}

IMAGE=""
VEX_DIR="security/vex"
FORMAT="table"
SEVERITY="HIGH,CRITICAL"
CONFIG_PREVIEW="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image)
            IMAGE="$2"; shift 2 ;;
        --vex-dir)
            VEX_DIR="$2"; shift 2 ;;
        --format)
            FORMAT="$2"; shift 2 ;;
        --severity)
            SEVERITY="$2"; shift 2 ;;
        --config-preview)
            CONFIG_PREVIEW="true"; shift ;;
        --help|-h)
            show_help; exit 0 ;;
        *)
            error "Unknown argument: $1"
            show_help
            exit 2 ;;
    esac
done

if [[ -z "${IMAGE}" ]]; then
    error "--image is required"
    show_help
    exit 2
fi

case "${FORMAT}" in
    table|sarif) ;;
    *) fatal "--format must be table or sarif (got: ${FORMAT})" ;;
esac

section "Configuration"
print_kv "Image" "${IMAGE}"
print_kv "VEX directory" "${VEX_DIR}"
print_kv "Format" "${FORMAT}"
print_kv "Severity" "${SEVERITY}"

if [[ "${CONFIG_PREVIEW}" == "true" ]]; then
    info "Config preview requested; exiting without scanning."
    exit 0
fi

require_tools trivy

# vexctl is only required when VEX directory exists with content.
HAS_VEX="false"
if [[ -d "${VEX_DIR}" ]] && compgen -G "${VEX_DIR}/*" >/dev/null; then
    require_tools vexctl
    HAS_VEX="true"
fi

# Main Logic
section "Trivy scan"

trivy_format="table"
if [[ "${FORMAT}" == "sarif" ]]; then
    trivy_format="sarif"
fi

trivy_output="$(mktemp)"
trap 'rm -f "${trivy_output}"' EXIT

trivy image \
    --severity "${SEVERITY}" \
    --format "${trivy_format}" \
    --output "${trivy_output}" \
    "${IMAGE}"

if [[ "${HAS_VEX}" == "true" ]]; then
    section "VEX filter"
    filtered="$(mktemp)"
    trap 'rm -f "${trivy_output}" "${filtered}"' EXIT
    vexctl filter \
        --vex-dir "${VEX_DIR}" \
        --input "${trivy_output}" \
        --output "${filtered}"
    cat "${filtered}"
else
    info "No VEX statements found in ${VEX_DIR}; emitting unfiltered scan."
    cat "${trivy_output}"
fi

section "Scan Summary"
print_kv "Image" "${IMAGE}"
print_kv "Format" "${FORMAT}"
print_kv "VEX applied" "${HAS_VEX}"
