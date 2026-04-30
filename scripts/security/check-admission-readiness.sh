#!/usr/bin/env bash
#
# check-admission-readiness.sh - Pre-flight readiness probe for signature-
# verifying admission control. Probes Kyverno, verifies the expected
# ClusterPolicy is loaded, checks the trusted-root ConfigMap freshness, and
# dry-runs a synthetic admission request.

set -o errexit
set -o nounset
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"

show_help() {
    cat <<'EOF'
Usage: check-admission-readiness.sh [options]

Verify that signature-verifying admission control is wired up on the current
kubectl context.

Options:
  --mode <sigstore|notation>          Expected signing mode (default: sigstore)
  --policy-name <name>                Override ClusterPolicy name (default depends on --mode)
  --trust-root-namespace <ns>         Namespace holding the trusted-root ConfigMap (default: kyverno)
  --trust-root-configmap <name>       Trusted-root ConfigMap name (default: sigstore-trusted-root)
  --max-age-hours <int>               Warn if trusted-root older than N hours (default: 24)
  --config-preview                    Print resolved configuration and exit without probing
  --help                              Show this help and exit
EOF
}

MODE="sigstore"
POLICY_NAME=""
TRUST_ROOT_NS="kyverno"
TRUST_ROOT_CM="sigstore-trusted-root"
MAX_AGE_HOURS="24"
CONFIG_PREVIEW="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="$2"; shift 2 ;;
        --policy-name)
            POLICY_NAME="$2"; shift 2 ;;
        --trust-root-namespace)
            TRUST_ROOT_NS="$2"; shift 2 ;;
        --trust-root-configmap)
            TRUST_ROOT_CM="$2"; shift 2 ;;
        --max-age-hours)
            MAX_AGE_HOURS="$2"; shift 2 ;;
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

case "${MODE}" in
    sigstore) : "${POLICY_NAME:=verify-images-sigstore}" ;;
    notation) : "${POLICY_NAME:=verify-images-notation}" ;;
    *) fatal "--mode must be sigstore or notation (got: ${MODE})" ;;
esac

section "Configuration"
print_kv "Mode" "${MODE}"
print_kv "Policy name" "${POLICY_NAME}"
print_kv "Trust-root NS" "${TRUST_ROOT_NS}"
print_kv "Trust-root CM" "${TRUST_ROOT_CM}"
print_kv "Max age (hours)" "${MAX_AGE_HOURS}"

if [[ "${CONFIG_PREVIEW}" == "true" ]]; then
    info "Config preview requested; exiting without probing cluster."
    exit 0
fi

require_tools kubectl

# Main Logic
KYVERNO_OK="false"
POLICY_OK="false"
TRUST_ROOT_OK="false"
DRYRUN_OK="false"

section "Probing Kyverno"
if kubectl get crd clusterpolicies.kyverno.io >/dev/null 2>&1; then
    info "Kyverno CRDs present."
    KYVERNO_OK="true"
else
    error "Kyverno CRD clusterpolicies.kyverno.io not found."
fi

section "ClusterPolicy"
if [[ "${KYVERNO_OK}" == "true" ]] && kubectl get clusterpolicy "${POLICY_NAME}" >/dev/null 2>&1; then
    info "ClusterPolicy ${POLICY_NAME} is loaded."
    POLICY_OK="true"
else
    error "ClusterPolicy ${POLICY_NAME} is not loaded."
fi

section "Trusted-root ConfigMap"
if kubectl get configmap "${TRUST_ROOT_CM}" -n "${TRUST_ROOT_NS}" >/dev/null 2>&1; then
    cm_ts="$(kubectl get configmap "${TRUST_ROOT_CM}" -n "${TRUST_ROOT_NS}" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || true)"
    if [[ -n "${cm_ts}" ]]; then
        cm_epoch="$(date -d "${cm_ts}" +%s 2>/dev/null || echo 0)"
        now_epoch="$(date +%s)"
        age_hours=$(( (now_epoch - cm_epoch) / 3600 ))
        info "Trusted-root ConfigMap age: ${age_hours}h"
        if (( age_hours > MAX_AGE_HOURS )); then
            warn "Trusted-root ConfigMap older than ${MAX_AGE_HOURS}h."
        else
            TRUST_ROOT_OK="true"
        fi
    else
        warn "Could not read creationTimestamp on trusted-root ConfigMap."
    fi
else
    error "Trusted-root ConfigMap ${TRUST_ROOT_NS}/${TRUST_ROOT_CM} not found."
fi

section "Synthetic admission dry-run"
synthetic_pod="$(cat <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: admission-readiness-probe
  namespace: default
spec:
  containers:
    - name: probe
      image: ghcr.io/example/unsigned:latest
YAML
)"
if echo "${synthetic_pod}" | kubectl create --dry-run=server -f - >/dev/null 2>&1; then
    warn "Server-side dry-run admitted an unsigned image; policy may be in audit mode."
    DRYRUN_OK="true"
else
    info "Server-side dry-run rejected the unsigned probe (expected for enforce mode)."
    DRYRUN_OK="true"
fi

section "Readiness Summary"
print_kv "Kyverno present" "${KYVERNO_OK}"
print_kv "Policy loaded" "${POLICY_OK}"
print_kv "Trust-root fresh" "${TRUST_ROOT_OK}"
print_kv "Dry-run executed" "${DRYRUN_OK}"

if [[ "${KYVERNO_OK}" != "true" || "${POLICY_OK}" != "true" || "${TRUST_ROOT_OK}" != "true" ]]; then
    fatal "Admission control is not ready."
fi

info "Admission control is ready."
