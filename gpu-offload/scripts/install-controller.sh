#!/usr/bin/env bash
# Install the admission controller in the resolved cluster
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"

# build-controller-image.sh always tags the image localhost/xavier-mutate:local,
# and load-images.sh imports that exact archive/tag straight into the node's
# containerd for every runtime (k3s and kind alike) -- it is never pushed to the
# host-local registry under any other reference. Referencing it as
# $registry_host/xavier-mutate:local would ask the cluster to pull an image that
# was never published there, so this always uses the imported tag directly.
helm --kube-context "$GPU_OFFLOAD_KUBE_CONTEXT" upgrade --install gpu-offload helm/gpu-offload \
  --namespace gpu-offload \
  --create-namespace \
  --set image.registry=localhost \
  --set mutate.image.repository=xavier-mutate \
  --set mutate.image.tag=local \
  --set image.pullPolicy=Never
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status deployment/gpu-offload-mutate \
  --namespace gpu-offload \
  --timeout=180s

# A Ready pod does not guarantee that the webhook Service is routable. Retry an
# actual mutation because failurePolicy: Fail blocks offload-labeled workloads.
probe_name="gpu-offload-webhook-probe-$$"
probe_manifest="$(mktemp --suffix=-webhook-probe.yaml)"
cleanup() {
  rm -f "$probe_manifest"
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete configmap "$probe_name" \
    --namespace gpu-offload --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" create configmap "$probe_name" \
  --namespace gpu-offload \
  --from-literal=remote.yaml=$'encryption: false\nnoserverdeployment: true\n' \
  --dry-run=client -o yaml |
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply -f - >/dev/null

cat > "$probe_manifest" << YAML
apiVersion: v1
kind: Pod
metadata:
  name: $probe_name
  namespace: gpu-offload
  annotations:
    xavierconfig: |
      remoteablecm: $probe_name
  labels:
    xavier: "true"
spec:
  containers:
    - name: probe
      image: probe
      command: ["true"]
      env:
        - name: REMOTERPORT
          value: "30000"
YAML

attempt=0
until mutation_marker="$(
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply --dry-run=server -f "$probe_manifest" \
    -o jsonpath='{.spec.containers[0].env[?(@.name=="XAVIER_CONTAINER")].value}' 2>/dev/null
)"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 15 ]; then
    echo "Mutating webhook still unreachable after ${attempt}s; last attempt:" >&2
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply --dry-run=server -f "$probe_manifest" -o yaml
    exit 1
  fi
  sleep 1
done

if [[ "$mutation_marker" != "true" ]]; then
  echo "Mutating webhook admitted the probe without applying the GPU-offload mutation" >&2
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply --dry-run=server -f "$probe_manifest" -o yaml
  exit 1
fi
