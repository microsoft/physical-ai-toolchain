#!/usr/bin/env bash
# Create the cluster for the resolved platform and runtime
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"

if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
  scripts/install-k3s.sh --context "$GPU_OFFLOAD_KUBE_CONTEXT"
  exit 0
fi

config_file="$(mktemp --suffix=-gpu-offload-kind.yaml)"
trap 'rm -f "$config_file"' EXIT

if [ "$GPU_OFFLOAD_PLATFORM" = "wsl-nvidia" ]; then
  test -c /dev/dxg
  test -d /usr/lib/wsl
  cat >"$config_file" <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraMounts:
      - hostPath: /dev/dxg
        containerPath: /dev/dxg
      - hostPath: /usr/lib/wsl
        containerPath: /usr/lib/wsl
        readOnly: true
EOF
else
  cat >"$config_file" <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
EOF
fi

KIND_EXPERIMENTAL_PROVIDER=podman kind create cluster \
  --name "$GPU_OFFLOAD_CLUSTER_NAME" \
  --image kindest/node:v1.35.0 \
  --config="$config_file"
kubectl config use-context "$GPU_OFFLOAD_KUBE_CONTEXT"
kubectl wait \
  --for=condition=Ready \
  "node/${GPU_OFFLOAD_CLUSTER_NAME}-control-plane" \
  --timeout=180s
