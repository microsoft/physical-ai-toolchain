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

if [ "$GPU_OFFLOAD_PLATFORM" = "wsl-nvidia" ]; then
  archive="$(mktemp --suffix=-nvidia-cuda.tar)"
  podman pull docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04
  podman save --output "$archive" docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04
  KIND_EXPERIMENTAL_PROVIDER=podman kind load image-archive "$archive" \
    --name "$GPU_OFFLOAD_CLUSTER_NAME"
  rm -f "$archive"
  kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: gpu-check
spec:
  restartPolicy: Never
  containers:
    - name: cuda
      image: docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04
      imagePullPolicy: IfNotPresent
      command: ["/bin/sh", "-c"]
      args:
        - |
          driver_dir=$(find /usr/lib/wsl/drivers -mindepth 1 -maxdepth 1 -type d | head -n 1)
          export LD_LIBRARY_PATH="/usr/lib/wsl/lib:${driver_dir}"
          test -c /dev/dxg
          /usr/lib/wsl/lib/nvidia-smi
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
      image: docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04
      command: ["nvidia-smi"]
      resources:
        limits:
          nvidia.com/gpu: "1"
EOF
fi

kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait pod/gpu-check \
  --for=jsonpath='{.status.phase}'=Succeeded \
  --timeout=300s
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs pod/gpu-check
