#!/usr/bin/env bash
# Run a GPU-allocated Kubernetes smoke check
# cspell:ignore dxg
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "Platform $GPU_OFFLOAD_PLATFORM does not use a GPU; skipping"
  exit 0
fi

node="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get nodes -o jsonpath='{.items[0].metadata.name}')"
gpu_allocatable="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get node "$node" \
  -o jsonpath='{.status.allocatable.nvidia\.com/gpu}')"
if [ -z "$gpu_allocatable" ] || [ "$gpu_allocatable" = "0" ]; then
  echo "No allocatable NVIDIA GPU; run: mise run cluster-30-gpu-enable" >&2
  exit 1
fi
echo "Allocatable nvidia.com/gpu: $gpu_allocatable"

cleanup() {
  exit_code=$?
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" delete pod/gpu-check --ignore-not-found >/dev/null
  exit "$exit_code"
}
trap cleanup EXIT

if [ "$GPU_OFFLOAD_PLATFORM" = "wsl-nvidia" ] && [ "$GPU_OFFLOAD_RUNTIME" = "kind" ]; then
  archive="$(mktemp --suffix=-nvidia-cuda.tar)"
  podman pull docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:133c78a0575303be34164d0b90137a042172bdf60696af01a3c424ab402d86e2
  podman save --output "$archive" docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:133c78a0575303be34164d0b90137a042172bdf60696af01a3c424ab402d86e2
  KIND_EXPERIMENTAL_PROVIDER=podman kind load image-archive "$archive" \
    --name "$GPU_OFFLOAD_CLUSTER_NAME"
  rm -f "$archive"
fi

if [ "$GPU_OFFLOAD_PLATFORM" = "wsl-nvidia" ]; then
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: gpu-check
spec:
  restartPolicy: Never
  containers:
    - name: cuda
      image: docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:133c78a0575303be34164d0b90137a042172bdf60696af01a3c424ab402d86e2
      imagePullPolicy: IfNotPresent
      command: ["/bin/sh", "-c"]
      args:
        - |
          test -c /dev/dxg
          nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
      env:
        - name: NVIDIA_VISIBLE_DEVICES
          value: all
        - name: NVIDIA_DRIVER_CAPABILITIES
          value: all
      resources:
        limits:
          nvidia.com/gpu: "1"
EOF
else
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: gpu-check
spec:
  restartPolicy: Never
  containers:
    - name: cuda
      image: docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04@sha256:133c78a0575303be34164d0b90137a042172bdf60696af01a3c424ab402d86e2
      command: ["nvidia-smi"]
      resources:
        limits:
          nvidia.com/gpu: "1"
EOF
fi

for ((attempt = 1; attempt <= 150; attempt++)); do
  phase="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get pod/gpu-check \
    -o jsonpath='{.status.phase}')"
  if [[ "$phase" == "Succeeded" ]]; then
    break
  fi
  if [[ "$phase" == "Failed" ]]; then
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs pod/gpu-check >&2 || true
    echo "GPU smoke pod failed" >&2
    exit 1
  fi
  ((attempt < 150)) || {
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" describe pod/gpu-check >&2
    echo "Timed out waiting for GPU smoke pod completion" >&2
    exit 1
  }
  sleep 2
done
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs pod/gpu-check
