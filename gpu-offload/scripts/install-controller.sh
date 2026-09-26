#!/usr/bin/env bash
# Install and verify the GPU offload admission controller
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Install the GPU offload admission controller in the resolved cluster and verify
that the mutating webhook applies the expected runtime injection.

OPTIONS:
    -h, --help               Show this help message
    --context NAME           Override the auto-detected Kubernetes context
    --namespace NAME         Deployment namespace (default: gpu-offload)
    --rollout-timeout VALUE  Controller rollout timeout (default: 180s)
    --probe-attempts COUNT   Admission probe attempts (default: 15)
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --context kind-gpu-offload --config-preview
EOF
}

# Defaults
context_override=""
namespace="${GPU_OFFLOAD_NAMESPACE:-gpu-offload}"
release_name="${GPU_OFFLOAD_RELEASE_NAME:-gpu-offload}"
rollout_timeout="${GPU_OFFLOAD_ROLLOUT_TIMEOUT:-180s}"
probe_attempts="${GPU_OFFLOAD_PROBE_ATTEMPTS:-15}"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    --context)             context_override="$2"; shift 2 ;;
    --namespace)           namespace="$2"; shift 2 ;;
    --rollout-timeout)     rollout_timeout="$2"; shift 2 ;;
    --probe-attempts)      probe_attempts="$2"; shift 2 ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

require_tools helm kubectl

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

eval "$("$SCRIPT_DIR/detect-platform.sh" --export)"
kube_context="${context_override:-$GPU_OFFLOAD_KUBE_CONTEXT}"
chart_path="$(cd "$SCRIPT_DIR/../helm/gpu-offload" && pwd)"
controller_image="localhost/xavier-mutate:local"

[[ -n "$kube_context" ]] || fatal "Kubernetes context cannot be empty"
[[ -n "$namespace" ]] || fatal "Namespace cannot be empty"
[[ "$probe_attempts" =~ ^[1-9][0-9]*$ ]] || fatal "--probe-attempts must be a positive integer"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Kubernetes context" "$kube_context"
  print_kv "Namespace" "$namespace"
  print_kv "Helm release" "$release_name"
  print_kv "Chart" "$chart_path"
  print_kv "Controller image" "$controller_image"
  print_kv "Rollout timeout" "$rollout_timeout"
  print_kv "Admission probe attempts" "$probe_attempts"
  exit 0
fi

#------------------------------------------------------------------------------
# Deploy Controller
#------------------------------------------------------------------------------

section "Deploy GPU Offload Controller"

# The image is imported directly into each cluster node by load-images.sh under
# this exact tag; it is not published through the host-local registry.
helm --kube-context "$kube_context" upgrade --install "$release_name" "$chart_path" \
  --namespace "$namespace" \
  --create-namespace \
  --set image.registry=localhost \
  --set mutate.image.repository=xavier-mutate \
  --set mutate.image.tag=local \
  --set image.pullPolicy=Never

info "Waiting for the admission controller rollout"
kubectl --context "$kube_context" rollout status "deployment/${release_name}-mutate" \
  --namespace "$namespace" \
  --timeout="$rollout_timeout"

#------------------------------------------------------------------------------
# Verify Admission Mutation
#------------------------------------------------------------------------------

section "Verify Admission Mutation"

probe_name="gpu-offload-webhook-probe-$$"
probe_manifest="$(mktemp --suffix=-webhook-probe.yaml)"

cleanup() {
  rm -f "$probe_manifest"
  kubectl --context "$kube_context" delete configmap "$probe_name" \
    --namespace "$namespace" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl --context "$kube_context" create configmap "$probe_name" \
  --namespace "$namespace" \
  --from-literal=remote.yaml=$'encryption: false\nserverstages:\n  - name: ""\n    noserverdeployment: true\n' \
  --dry-run=client -o yaml |
  kubectl --context "$kube_context" apply -f - >/dev/null

cat > "$probe_manifest" << YAML
apiVersion: v1
kind: Pod
metadata:
  name: $probe_name
  namespace: $namespace
  annotations:
    xavierconfig: |
      remoteablecm: $probe_name
  labels:
    xavier: "true"
spec:
  containers:
    - name: probe
      image: probe # pinning-ignore: server-side admission dry-run; no image is pulled
      command: ["true"]
      env:
        - name: REMOTERPORT
          value: "30000"
YAML

attempt=0
until mutation_marker="$(
  kubectl --context "$kube_context" apply --dry-run=server -f "$probe_manifest" \
    -o jsonpath='{.spec.containers[0].env[?(@.name=="XAVIER_CONTAINER")].value}' 2>/dev/null
)"; do
  attempt=$((attempt + 1))
  if [[ "$attempt" -ge "$probe_attempts" ]]; then
    kubectl --context "$kube_context" apply --dry-run=server -f "$probe_manifest" -o yaml
    fatal "Mutating webhook remains unreachable after $attempt attempts"
  fi
  sleep 1
done

if [[ "$mutation_marker" != "true" ]]; then
  kubectl --context "$kube_context" apply --dry-run=server -f "$probe_manifest" -o yaml
  fatal "Mutating webhook admitted the probe without applying the GPU offload mutation"
fi

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Kubernetes context" "$kube_context"
print_kv "Namespace" "$namespace"
print_kv "Helm release" "$release_name"
print_kv "Controller image" "$controller_image"
print_kv "Admission mutation" "Verified"
info "GPU offload controller deployment complete"
