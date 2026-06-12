#!/usr/bin/env bash
#
# verify-image.sh - Verify a container image signature using Sigstore (cosign)
# or Notation (notation), with optional offline mode against a Flux-distributed
# trusted-root bundle.
#
# Wraps `cosign verify` and `notation verify` behind one operator UX. Supports
# auto-detection of the signature system, offline verification, and a
# --config-preview dry-run.

set -o errexit
set -o nounset
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

# Defaults pinned to this repo's container-publish.yml workflow ref.
DEFAULT_IDENTITY_REGEXP='^https://github\.com/microsoft/physical-ai-toolchain/\.github/workflows/container-publish\.yml@refs/(heads/main|tags/v.+)$'
DEFAULT_OIDC_ISSUER='https://token.actions.githubusercontent.com'

show_help() {
    cat <<'EOF'
Usage: verify-image.sh --image <ref> [options]

Verify a container image signature with cosign (Sigstore) or notation.

Required:
  --image <ref>                       Image reference (registry/repo@sha256:... or :tag)

Options:
  --mode <sigstore|notation|auto>     Signature system to verify (default: auto)
  --offline                           Verify without contacting Rekor / TUF; uses --trusted-root bundle
  --trusted-root <path>               Path to Sigstore trusted-root JSON (offline mode)
  --policy-file <path>                Notation trust policy file (notation mode)
  --certificate-identity-regexp <re>  Cosign identity regexp (default: pinned to this repo)
  --certificate-oidc-issuer <url>     Cosign OIDC issuer (default: GitHub Actions)
  --accept-public-rekor               Acknowledge that online Sigstore verification will
                                      query the public Rekor transparency log (required
                                      when --mode=sigstore without --offline)
  --config-preview                    Print resolved configuration and exit without verifying
  --help                              Show this help and exit
EOF
}

# Defaults
IMAGE=""
MODE="auto"
OFFLINE="false"
TRUSTED_ROOT=""
POLICY_FILE=""
IDENTITY_REGEXP="${DEFAULT_IDENTITY_REGEXP}"
OIDC_ISSUER="${DEFAULT_OIDC_ISSUER}"
CONFIG_PREVIEW="false"
ACCEPT_PUBLIC_REKOR="false"

# Argument parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        --image)
            IMAGE="$2"; shift 2 ;;
        --mode)
            MODE="$2"; shift 2 ;;
        --offline)
            OFFLINE="true"; shift ;;
        --trusted-root)
            TRUSTED_ROOT="$2"; shift 2 ;;
        --policy-file)
            POLICY_FILE="$2"; shift 2 ;;
        --certificate-identity-regexp)
            IDENTITY_REGEXP="$2"; shift 2 ;;
        --certificate-oidc-issuer)
            OIDC_ISSUER="$2"; shift 2 ;;
        --accept-public-rekor)
            ACCEPT_PUBLIC_REKOR="true"; shift ;;
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

case "${MODE}" in
    sigstore|notation|auto) ;;
    *)
        fatal "--mode must be one of: sigstore, notation, auto (got: ${MODE})" ;;
esac

# Gather Configuration
section "Configuration"
print_kv "Image" "${IMAGE}"
print_kv "Mode" "${MODE}"
print_kv "Offline" "${OFFLINE}"
print_kv "Trusted root" "${TRUSTED_ROOT:-<none>}"
print_kv "Policy file" "${POLICY_FILE:-<none>}"
print_kv "Identity regexp" "${IDENTITY_REGEXP}"
print_kv "OIDC issuer" "${OIDC_ISSUER}"
print_kv "Public Rekor consent" "${ACCEPT_PUBLIC_REKOR}"

if [[ "${CONFIG_PREVIEW}" == "true" ]]; then
    info "Config preview requested; exiting without verification."
    exit 0
fi

# Tool requirements depend on resolved mode.
detect_mode() {
    if command -v cosign >/dev/null 2>&1 && cosign tree "${IMAGE}" >/dev/null 2>&1; then
        echo "sigstore"; return
    fi
    if command -v notation >/dev/null 2>&1 && notation list "${IMAGE}" >/dev/null 2>&1; then
        echo "notation"; return
    fi
    echo "unknown"
}

RESOLVED_MODE="${MODE}"
if [[ "${MODE}" == "auto" ]]; then
    RESOLVED_MODE="$(detect_mode)"
    if [[ "${RESOLVED_MODE}" == "unknown" ]]; then
        fatal "auto-detect failed: no cosign tree or notation list output for ${IMAGE}"
    fi
    info "Auto-detected signature mode: ${RESOLVED_MODE}"
fi

case "${RESOLVED_MODE}" in
    sigstore)
        require_tools cosign
        ;;
    notation)
        require_tools notation
        ;;
esac

if [[ "${OFFLINE}" == "true" && -n "${TRUSTED_ROOT}" && ! -f "${TRUSTED_ROOT}" ]]; then
    fatal "trusted-root file not found: ${TRUSTED_ROOT}"
fi

# Public Rekor consent gate. Online Sigstore verification queries the public
# Rekor transparency log, which permanently publishes signer identity and image
# digest. Operators must opt in explicitly; --offline bypasses Rekor and is
# always allowed.
if [[ "${RESOLVED_MODE}" == "sigstore" && "${OFFLINE}" != "true" && "${ACCEPT_PUBLIC_REKOR}" != "true" ]]; then
    warn "============================================================="
    warn " Public Rekor transparency log disclosure required"
    warn "-------------------------------------------------------------"
    warn " Online Sigstore verification will query the public Rekor log"
    warn " (rekor.sigstore.dev). The signer identity, OIDC issuer, and"
    warn " image digest become permanently and publicly visible."
    warn ""
    warn " To proceed, re-run with --accept-public-rekor."
    warn " To verify without contacting Rekor, re-run with --offline"
    warn " and a Sigstore trusted-root bundle (--trusted-root <path>)."
    warn "============================================================="
    fatal "Public Rekor consent not granted; aborting."
fi

# Main Logic
section "Verification"

verify_sigstore() {
    local args=(verify
        --certificate-identity-regexp="${IDENTITY_REGEXP}"
        --certificate-oidc-issuer="${OIDC_ISSUER}")
    if [[ "${OFFLINE}" == "true" ]]; then
        args+=(--offline)
        if [[ -n "${TRUSTED_ROOT}" ]]; then
            args+=(--trusted-root="${TRUSTED_ROOT}")
        fi
    fi
    args+=("${IMAGE}")
    info "cosign ${args[*]}"
    cosign "${args[@]}"
}

verify_notation() {
    local args=(verify)
    if [[ -n "${POLICY_FILE}" ]]; then
        args+=(--policy-file "${POLICY_FILE}")
    fi
    args+=("${IMAGE}")
    info "notation ${args[*]}"
    notation "${args[@]}"
}

case "${RESOLVED_MODE}" in
    sigstore) verify_sigstore ;;
    notation) verify_notation ;;
    *) fatal "unsupported resolved mode: ${RESOLVED_MODE}" ;;
esac

section "Verification Summary"
print_kv "Image" "${IMAGE}"
print_kv "Mode" "${RESOLVED_MODE}"
print_kv "Offline" "${OFFLINE}"
print_kv "Result" "verified"
