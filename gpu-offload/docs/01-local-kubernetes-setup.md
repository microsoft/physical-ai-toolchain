---
title: Local Kubernetes Setup for GPU Offload
description: Prepare a kind or k3s cluster for CPU-only, WSL NVIDIA, or bare-metal NVIDIA GPU offload verification
ms.date: 2026-08-18
ms.topic: get-started
---

<!-- cspell:ignore crun userns -->

Prepare a local cluster for the first GPU-offload run. Three paths are supported:

| Path              | Cluster runtime | Use when                                                    |
|-------------------|-----------------|-------------------------------------------------------------|
| CPU only          | kind + Podman   | Any Linux host, or to force CPU on a machine that has a GPU |
| WSL NVIDIA        | kind + Podman   | WSL2 with a GPU exposed through `/dev/dxg`                  |
| Bare-metal NVIDIA | k3s             | Native Linux with an NVIDIA driver and `/dev/nvidia*`       |

Bare-metal NVIDIA uses k3s rather than kind because kind runs the Kubernetes node as a container, which would require passing the GPU through twice. k3s runs on the host, so GPU access is the ordinary documented case.

Every command below remains standalone. The `mise` tasks in `gpu-offload/mise.toml` automate the same sequence and select the correct path automatically.

## Run the Automated Path

Run all tasks from the `gpu-offload` directory:

```bash
cd gpu-offload
mise trust
mise run a-detect
mise run f-10-setup
mise run f-20-verify
```

`mise run a-detect` prints the resolved platform before anything changes:

```text
Platform:          baremetal-nvidia (auto-detected)
Cluster runtime:   k3s (auto-detected)
GPU enabled:       true
Server stage:      nvidia
Cluster name:      gpu-offload-k3s
Kube context:      gpu-offload-k3s
```

### Override Auto-Detection

Auto-detection is skipped for any value set in `gpu-offload/.env`. Create one to force a path, such as running CPU on a machine that has a working GPU:

```bash
cp .env.example .env
```

| Variable               | Values                                  | Effect                     |
|------------------------|-----------------------------------------|----------------------------|
| `GPU_OFFLOAD_PLATFORM` | `cpu`, `wsl-nvidia`, `baremetal-nvidia` | Forces the platform path   |
| `GPU_OFFLOAD_RUNTIME`  | `kind`, `k3s`                           | Forces the cluster runtime |

Setting `GPU_OFFLOAD_PLATFORM=cpu` selects the kind CPU path and skips all GPU configuration, even when a GPU is present.

## Clone the Repository

Clone the repository and enter its root directory:

```bash
git clone https://github.com/microsoft/physical-ai-toolchain.git
cd physical-ai-toolchain
```

Existing clones must run the remaining commands from the repository root:

```bash
git pull --ff-only
git rev-parse --show-toplevel
```

## Prerequisites

| Tool                     | Validated version               | Verify                 |
|--------------------------|---------------------------------|------------------------|
| Ubuntu                   | 24.04 on Linux or WSL2          | `cat /etc/os-release`  |
| Podman                   | 4.9.3 or later                  | `podman version`       |
| kind                     | 0.30.0                          | `kind version`         |
| Kubernetes               | 1.35.0                          | `kubectl version`      |
| Helm                     | 3.21.3                          | `helm version`         |
| NVIDIA Container Toolkit | 1.19.1 or later for NVIDIA only | `nvidia-ctk --version` |

### Ref 01: Install Host Packages

Install the base host packages:

```bash
sudo apt-get update
sudo apt-get install --yes curl jq podman
podman info --format '{{.Host.Security.Rootless}} {{.Host.OCIRuntime.Name}}'
```

The final command must print `true crun`.

### Ref 02: Install Kubernetes Tools

Install kind, kubectl, and Helm with mise when they are not already available:

```bash
mise use --global kind@0.30.0 kubectl@1.35.1 helm@3.21.3
eval "$(mise activate bash)"
kind version
kubectl version --client
helm version
```

### Ref 10: Validate Prerequisites

Verify the installed tools and render the controller chart before creating a cluster:

```bash
podman info --format '{{.Host.Security.Rootless}} {{.Host.OCIRuntime.Name}}'
kind version
kubectl version --client
helm version
helm template gpu-offload gpu-offload/helm/gpu-offload \
  --namespace gpu-offload \
  --set image.registry=localhost >/dev/null
```

## Podman kind CPU Only

### Ref 20: Set Up the CPU Cluster

Create a single-node cluster without NVIDIA configuration:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman

kind create cluster \
  --name gpu-offload \
  --image kindest/node:v1.35.0

kubectl config use-context kind-gpu-offload
kubectl wait \
  --for=condition=Ready \
  node/gpu-offload-control-plane \
  --timeout=120s
kubectl get nodes -o wide
```

### Ref 21: Run the CPU Check

Confirm that Podman and Kubernetes run CPU workloads:

```bash
podman run --rm docker.io/library/alpine:3.22 \
  sh -c 'uname -m; echo Podman CPU container works'

kubectl run cpu-check \
  --image=docker.io/library/alpine:3.22 \
  --restart=Never \
  --command -- sh -c 'uname -m; echo Kubernetes CPU pod works'
kubectl wait pod/cpu-check \
  --for=jsonpath='{.status.phase}'=Succeeded \
  --timeout=120s
kubectl logs pod/cpu-check
kubectl delete pod/cpu-check
```

Continue to the [CPU-only offload](./02-first-local-offload.md#podman-kind-cpu-only).

## Podman kind NVIDIA on WSL2

Use this path on WSL2 when the Windows NVIDIA driver exposes `/dev/dxg`. Do not install a Linux display driver in the WSL distribution.

> [!IMPORTANT]
> The WSL2 path uses a generic Kubernetes device plugin for `/dev/dxg`. NVIDIA's standard device plugin requires NVML behavior that is not available in this nested WSL2 and kind topology.

### Ref 30: Install NVIDIA Container Toolkit

Install NVIDIA Container Toolkit from NVIDIA's apt repository:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor --yes \
  --output /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null

sudo apt-get update
sudo apt-get install --yes nvidia-container-toolkit
sudo mkdir -p /etc/cdi
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
```

The CDI list must include `nvidia.com/gpu=all`.

### Ref 31: Verify Podman GPU Access

Verify WSL and rootless Podman before creating Kubernetes:

```bash
nvidia-smi
test -c /dev/dxg

podman run --rm \
  --security-opt=label=disable \
  --device nvidia.com/gpu=all \
  docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04 \
  nvidia-smi
```

Both `nvidia-smi` commands must list the same adapter.

### Ref 32: Set Up the NVIDIA kind Cluster

Create the kind node with the WSL device and driver directory mounted into it:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman

cat <<'EOF' >/tmp/gpu-offload-kind.yaml
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

kind create cluster \
  --name gpu-offload-nvidia \
  --image kindest/node:v1.35.0 \
  --config=/tmp/gpu-offload-kind.yaml

kubectl config use-context kind-gpu-offload-nvidia
kubectl wait \
  --for=condition=Ready \
  node/gpu-offload-nvidia-control-plane \
  --timeout=120s
```

### Ref 33: Verify NVIDIA Node Access

Confirm GPU access inside the kind node:

```bash
podman exec gpu-offload-nvidia-control-plane sh -c \
  'driver_dir=$(find /usr/lib/wsl/drivers -mindepth 1 -maxdepth 1 -type d | head -n 1); LD_LIBRARY_PATH="/usr/lib/wsl/lib:${driver_dir}" /usr/lib/wsl/lib/nvidia-smi'
```

### Ref 34: Configure the Node Runtime

Add the WSL driver directory to kind's existing OCI base specification. Containerd reads this file at startup, so restart it after the update:

```bash
podman exec gpu-offload-nvidia-control-plane sh -c \
  'jq '\''if any(.mounts[]; .destination == "/usr/lib/wsl") then . else .mounts += [{"destination":"/usr/lib/wsl","type":"none","source":"/usr/lib/wsl","options":["rbind","ro","nosuid","nodev"]}] end'\'' /etc/containerd/cri-base.json > /etc/containerd/cri-base.json.new && mv /etc/containerd/cri-base.json.new /etc/containerd/cri-base.json'

podman exec gpu-offload-nvidia-control-plane systemctl restart containerd
kubectl wait \
  --context kind-gpu-offload-nvidia \
  --for=condition=Ready \
  node/gpu-offload-nvidia-control-plane \
  --timeout=180s
```

This local runtime configuration makes the read-only WSL driver tree available to every container in the kind node. Do not use it as a production Kubernetes configuration.

### Ref 35: Register the WSL GPU

Load the pinned generic device plugin image into kind:

```bash
podman pull docker.io/squat/generic-device-plugin:0.2.0
podman save \
  --output /tmp/generic-device-plugin-0.2.0.tar \
  docker.io/squat/generic-device-plugin:0.2.0

kind load image-archive \
  /tmp/generic-device-plugin-0.2.0.tar \
  --name gpu-offload-nvidia
```

Register `/dev/dxg` as one `nvidia.com/gpu` resource:

```bash
kubectl --context kind-gpu-offload-nvidia apply -f - <<'EOF'
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

kubectl --context kind-gpu-offload-nvidia rollout status daemonset/wsl-gpu-device-plugin \
  --namespace kube-system \
  --timeout=120s

kubectl --context kind-gpu-offload-nvidia get nodes \
  -o custom-columns='NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'
```

Wait for the `GPU` column to report `1` before continuing.

### Ref 36: Verify Kubernetes GPU Access

Load the CUDA image into kind and run a pod that requests the registered resource:

```bash
podman pull docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04
podman save \
  --output /tmp/nvidia-cuda-12.8.1.tar \
  docker.io/nvidia/cuda:12.8.1-base-ubuntu24.04

kind load image-archive \
  /tmp/nvidia-cuda-12.8.1.tar \
  --name gpu-offload-nvidia

kubectl --context kind-gpu-offload-nvidia apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: wsl-gpu-check
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

kubectl --context kind-gpu-offload-nvidia wait pod/wsl-gpu-check \
  --for=jsonpath='{.status.phase}'=Succeeded \
  --timeout=120s
kubectl --context kind-gpu-offload-nvidia logs pod/wsl-gpu-check
kubectl --context kind-gpu-offload-nvidia delete pod/wsl-gpu-check
```

The log must list the NVIDIA adapter. Continue to the [NVIDIA offload](./02-first-local-offload.md#podman-kind-nvidia-on-wsl2).

## Bare-Metal NVIDIA with k3s

Use this path on a native Linux install where the NVIDIA driver is loaded and `/dev/nvidia*` exists. Do not use it in WSL2.

Confirm the host first:

```bash
nvidia-smi
ls /dev/nvidia*
grep -qi microsoft /proc/version && echo "WSL detected - use the WSL path" || echo "bare metal"
```

### Install the NVIDIA Container Toolkit

Install the toolkit exactly as in the WSL path, then generate the CDI specification:

```bash
sudo apt-get install --yes nvidia-container-toolkit
sudo mkdir -p /etc/cdi
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
```

> [!NOTE]
> Podman 4.9.3 cannot parse the CDI 0.7.0 specification that current `nvidia-ctk` releases generate, so `podman run --device nvidia.com/gpu=all` may fail with `unresolvable CDI devices`. This does not affect k3s, which reads the runtime directly. On the bare-metal path this check is advisory; Podman is used only to build images.

### Create the Cluster

Install k3s and register its kubeconfig context:

```bash
mise run c-cluster-20-create
```

The task pins the k3s version, verifies the installer checksum, and merges the kubeconfig as the context `gpu-offload-k3s`. It renames the cluster, user, and context away from k3s's default name of `default` so a later reinstall replaces the entry instead of merging against a stale certificate authority.

### Register the GPU

```bash
mise run c-cluster-30-gpu-enable
```

This makes the NVIDIA runtime the containerd default and installs the NVIDIA device plugin.

k3s regenerates its containerd configuration on every start, so the default runtime is set through a k3s configuration drop-in rather than by editing containerd files:

```bash
sudo mkdir -p /etc/rancher/k3s/config.yaml.d
sudo tee /etc/rancher/k3s/config.yaml.d/10-nvidia-default-runtime.yaml >/dev/null <<'EOF'
default-runtime: nvidia
EOF
sudo systemctl restart k3s
```

Confirm the setting reached the generated configuration:

```bash
sudo grep default_runtime_name /var/lib/rancher/k3s/agent/etc/containerd/config.toml
```

> [!IMPORTANT]
> The NVIDIA runtime is made the *default* rather than an opt-in `RuntimeClass` because the offload specification has no `runtimeClassName` field, so generated server Deployments cannot select a runtime handler themselves.

### Verify Kubernetes GPU Access

```bash
mise run c-cluster-31-gpu-check
```

The pod log must list the NVIDIA adapter, and the node must report `nvidia.com/gpu: 1` as allocatable. Continue to the [bare-metal NVIDIA offload](./02-first-local-offload.md).

### Control Whether k3s Starts on Boot

k3s installs as a systemd service that is enabled by default. Change that without uninstalling:

```bash
mise run c-cluster-82-service-disable
mise run c-cluster-81-service-enable
```

Both tasks leave the cluster running and report the resulting boot and runtime state. Use `mise run c-cluster-80-stop` and `mise run c-cluster-70-start` to stop or start the running cluster.

## Manage Existing Clusters

### Ref 00: List Clusters

List the Podman-backed kind clusters and their node containers:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman
kind get clusters
podman ps --all --filter label=io.x-k8s.kind.cluster
```

### Refs 70-71: Start a Cluster

Start a stopped cluster and wait for its node:

```bash
cluster_name=gpu-offload
podman start "${cluster_name}-control-plane"
kubectl config use-context "kind-${cluster_name}"
kubectl wait \
  --for=condition=Ready \
  "node/${cluster_name}-control-plane" \
  --timeout=120s
```

Use `gpu-offload` for the CPU cluster or `gpu-offload-nvidia` for the WSL2 NVIDIA cluster.

### Refs 80-81: Stop a Cluster

Stop a cluster without deleting its state:

```bash
cluster_name=gpu-offload
podman stop "${cluster_name}-control-plane"
```

## Troubleshooting

### The device plugin crashes with an invalid device discovery strategy

The plugin log ends with:

```text
Incompatible strategy detected auto
error starting plugins: ... invalid device discovery strategy
```

The container has no driver libraries, meaning it is not running under the NVIDIA runtime. Confirm the default runtime is applied, then delete the pod. A pod created before the runtime change keeps its original sandbox across `CrashLoopBackOff` restarts and can never pick up the new runtime:

```bash
sudo grep default_runtime_name /var/lib/rancher/k3s/agent/etc/containerd/config.toml
kubectl -n kube-system delete pod -l name=nvidia-device-plugin-ds
```

### A GPU Deployment rollout never completes

The rollout reports `1 old replicas are pending termination` and the new pod stays `Pending` with `Insufficient nvidia.com/gpu`.

A single exclusive GPU cannot be surged: the replacement pod waits for a device that the outgoing pod still holds. The GPU stage must replace rather than roll:

```bash
kubectl -n gpu-offload-demo patch deployment first-run-client-remote-server-nvidia \
  --type=merge \
  --patch '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}'
```

`mise run d-offload-50-deploy` applies this automatically whenever the GPU path is active.

### Reinstalling k3s fails TLS verification

`kubectl` reports:

```text
x509: certificate signed by unknown authority
```

A previous install left a cluster entry holding the old certificate authority, and the merge kept it. Remove the stale entries and recreate the cluster:

```bash
kubectl config delete-context gpu-offload-k3s
kubectl config delete-cluster default
kubectl config delete-user default
mise run c-cluster-20-create
```

### Admission fails with a 502 from the webhook

Deploying returns:

```text
failed calling webhook "mutate.gpu-offload.io": ... code 502: 502 Bad Gateway
```

The controller Service published the pod before its TLS listener was bound. The chart defines a readiness probe on the webhook port to prevent this. Confirm the probe exists and that the endpoint is ready:

```bash
kubectl -n gpu-offload get deploy gpu-offload-mutate \
  -o jsonpath='{.spec.template.spec.containers[0].readinessProbe}'
kubectl -n gpu-offload get endpointslice -l kubernetes.io/service-name=gpu-offload-mutate
```

### Pods cannot reach each other on Ubuntu 24.04

Verify the firewall is not blocking the pod and service networks, then test real traffic rather than inspecting interfaces:

```bash
sudo ufw status
kubectl run probe --image=nginx:1.27-alpine --restart=Never --command -- \
  sh -c 'nslookup kubernetes.default.svc.cluster.local && echo DNS OK'
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/probe --timeout=120s
kubectl logs probe && kubectl delete pod probe
```

`ufw` is the most common cause: enabling it without allowing the pod CIDR breaks pod-to-pod traffic and DNS.

> [!NOTE]
> Kubernetes normally refuses to start when swap is enabled, but k3s sets `failSwapOn: false`, so an active swap file does not need to be removed. Pods still run with `NoSwap`.

### Podman cannot create a user namespace

Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`, which can block rootless `podman build`:

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns
```

This restriction affects image builds only. k3s runs as root and is unaffected.

### kind selects another provider

Set the provider for every kind command:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman
```

### Podman cannot resolve the CDI device

Regenerate the system CDI file and verify its device names:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
nvidia-ctk cdi list
```

### Kubernetes reports no GPU capacity

Inspect the generic device plugin and the node resource state:

```bash
kubectl --context kind-gpu-offload-nvidia logs daemonset/wsl-gpu-device-plugin --namespace kube-system
kubectl --context kind-gpu-offload-nvidia describe node gpu-offload-nvidia-control-plane
podman exec gpu-offload-nvidia-control-plane ls -l /dev/dxg
```

The plugin log must show registration for `nvidia.com/gpu`, and `/dev/dxg` must exist in the node.

### The GPU pod cannot find WSL libraries

Confirm that the OCI base spec contains the read-only mount, then restart containerd:

```bash
podman exec gpu-offload-nvidia-control-plane \
  jq '.mounts[] | select(.destination == "/usr/lib/wsl")' \
  /etc/containerd/cri-base.json

podman exec gpu-offload-nvidia-control-plane systemctl restart containerd
kubectl wait \
  --context kind-gpu-offload-nvidia \
  --for=condition=Ready \
  node/gpu-offload-nvidia-control-plane \
  --timeout=180s
```

## Cleanup

### Remove the Offload Workloads

Remove the demo and controller while leaving the cluster installed and running:

```bash
mise run f-30-teardown
```

### Delete the Cluster

Remove the workloads and the cluster itself:

```bash
mise run f-40-teardown-all
```

On the bare-metal path this uninstalls k3s through `/usr/local/bin/k3s-uninstall.sh` and removes the `gpu-offload-k3s` kubeconfig entries. Prefer `mise run c-cluster-82-service-disable` when the goal is only to stop k3s from starting on boot.

### Ref 90: Tear Down the CPU Cluster

Delete the CPU cluster:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman
kind delete cluster --name gpu-offload
```

### Ref 91: Tear Down the NVIDIA Cluster

Delete the WSL2 NVIDIA cluster and its temporary kind configuration:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman
kind delete cluster --name gpu-offload-nvidia
rm -f \
  /tmp/gpu-offload-kind.yaml \
  /tmp/generic-device-plugin-0.2.0.tar \
  /tmp/nvidia-cuda-12.8.1.tar
```
