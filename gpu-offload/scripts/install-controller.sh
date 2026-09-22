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

# rollout status only confirms the pod is Ready; it says nothing about whether
# the Service is actually routable yet. Even once the Endpoints object reports
# the pod's address, kube-proxy still has to program the ClusterIP's netfilter
# rule on the node before traffic to it succeeds -- a real, observed race
# (confirmed in CI: the Endpoints wait reported ready, then the very next
# admission call still hit "connection refused" ~250ms later). failurePolicy:
# Fail means any miss blocks every offload-labeled pod/deployment/job, so
# retry a real admission call rather than trust an API object's state.
# --dry-run=server exercises the webhook (it's invoked for dry-run requests
# because the webhook declares sideEffects: None) without persisting anything,
# so this needs no cleanup.
probe_manifest="$(mktemp --suffix=-webhook-probe.yaml)"
trap 'rm -f "$probe_manifest"' EXIT
cat > "$probe_manifest" << 'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: gpu-offload-webhook-probe
  namespace: gpu-offload
  labels:
    xavier: "true"
spec:
  containers:
    - name: probe
      image: probe
      command: ["true"]
YAML

attempt=0
until kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply --dry-run=server -f "$probe_manifest" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 15 ]; then
    echo "Mutating webhook still unreachable after ${attempt}s; last attempt:" >&2
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply --dry-run=server -f "$probe_manifest"
    exit 1
  fi
  sleep 1
done
