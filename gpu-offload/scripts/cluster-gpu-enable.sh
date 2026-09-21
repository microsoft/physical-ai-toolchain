#!/usr/bin/env bash
# Expose the GPU to the cluster for the resolved platform
# cspell:ignore dxg nvml rbind nosuid nodev
set -o errexit -o nounset

cd "$(dirname "${BASH_SOURCE[0]}")/.."
eval "$(scripts/detect-platform.sh --export)"
if [ "$GPU_OFFLOAD_GPU_ENABLED" != "true" ]; then
  echo "Platform $GPU_OFFLOAD_PLATFORM does not use a GPU; skipping"
  exit 0
fi

if [ "$GPU_OFFLOAD_RUNTIME" = "k3s" ]; then
  scripts/configure-k3s-nvidia.sh --context "$GPU_OFFLOAD_KUBE_CONTEXT"
  exit 0
fi

node="${GPU_OFFLOAD_CLUSTER_NAME}-control-plane"

# kind's node containerd reads its OCI base spec at startup, so the read-only
# WSL driver tree must be added there before any GPU pod starts.
podman exec "$node" sh -c \
  'jq '\''if any(.mounts[]; .destination == "/usr/lib/wsl") then . else .mounts += [{"destination":"/usr/lib/wsl","type":"none","source":"/usr/lib/wsl","options":["rbind","ro","nosuid","nodev"]}] end'\'' /etc/containerd/cri-base.json > /etc/containerd/cri-base.json.new && mv /etc/containerd/cri-base.json.new /etc/containerd/cri-base.json'
podman exec "$node" systemctl restart containerd
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" wait \
  --for=condition=Ready "node/$node" --timeout=180s

archive="$(mktemp --suffix=-generic-device-plugin.tar)"
trap 'rm -f "$archive"' EXIT
podman pull docker.io/squat/generic-device-plugin:0.2.0
podman save --output "$archive" docker.io/squat/generic-device-plugin:0.2.0
KIND_EXPERIMENTAL_PROVIDER=podman kind load image-archive "$archive" \
  --name "$GPU_OFFLOAD_CLUSTER_NAME"

# NVML is unavailable in nested WSL2, so a generic device plugin advertises
# /dev/dxg as nvidia.com/gpu instead of NVIDIA's own plugin.
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" apply -f - <<'EOF'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: wsl-gpu-device-plugin
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: wsl-gpu-device-plugin
  template:
    metadata:
      labels:
        app: wsl-gpu-device-plugin
    spec:
      priorityClassName: system-node-critical
      tolerations:
        - operator: Exists
      containers:
        - name: device-plugin
          image: docker.io/squat/generic-device-plugin@sha256:66c8d5c270eb2b721f1064c549b9b7898152a6d2f0163380a5d37dc7636c20ff
          imagePullPolicy: IfNotPresent
          args:
            - --domain=nvidia.com
            - --device={"name":"gpu","groups":[{"paths":[{"path":"/dev/dxg"}]}]}
          securityContext:
            privileged: true
          volumeMounts:
            - name: device-plugins
              mountPath: /var/lib/kubelet/device-plugins
            - name: dxg
              mountPath: /dev/dxg
      volumes:
        - name: device-plugins
          hostPath:
            path: /var/lib/kubelet/device-plugins
        - name: dxg
          hostPath:
            path: /dev/dxg
            type: CharDevice
EOF

rollout_timeout="${GPU_PLUGIN_ROLLOUT_TIMEOUT:-600}"
register_timeout="${GPU_PLUGIN_REGISTER_TIMEOUT:-600}"
kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" rollout status \
  daemonset/wsl-gpu-device-plugin \
  --namespace kube-system \
  --timeout="${rollout_timeout}s"

# The rollout completes once the pod is Running, but kubelet only publishes
# nvidia.com/gpu after the plugin registers over its gRPC socket.
elapsed=0
while :; do
  gpu_allocatable="$(kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get node "$node" \
    -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null || true)"
  if [ "$gpu_allocatable" = "1" ]; then
    break
  fi
  if [ "$elapsed" -ge "$register_timeout" ]; then
    echo "Timed out waiting for nvidia.com/gpu; found: ${gpu_allocatable:-none}" >&2
    kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" logs --namespace kube-system \
      --selector app=wsl-gpu-device-plugin --tail=50 >&2 || true
    exit 1
  fi
  echo "Waiting for the device plugin to register nvidia.com/gpu (${elapsed}s/${register_timeout}s)..."
  sleep 5
  elapsed=$((elapsed + 5))
done

kubectl --context "$GPU_OFFLOAD_KUBE_CONTEXT" get nodes \
  -o custom-columns='NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'
