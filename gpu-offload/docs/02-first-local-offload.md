---
title: First Local Offload
description: Build and verify CPU-only or WSL NVIDIA remote execution with Podman and kind
ms.date: 2026-08-11
ms.topic: tutorial
---

Run a client function transparently in a generated server-stage pod. The example squares four integers and returns the hostname of the pod that executed the function. Reference numbers correspond to optional local automation while every command remains standalone.

## Prerequisites

Complete [Local Podman and kind Setup](./01-local-kubernetes-setup.md). Run every command from the repository root. Select `gpu-offload` for the CPU path or `gpu-offload-nvidia` for the WSL2 NVIDIA path:

```bash
export KIND_EXPERIMENTAL_PROVIDER=podman
export GPU_OFFLOAD_CLUSTER=gpu-offload-nvidia
kubectl config use-context "kind-${GPU_OFFLOAD_CLUSTER}"
```

## Ref 40: Build Images

Build the admission controller, the remoter payload image, and the client image with Podman.

The payload image is `FROM scratch` and only holds the `runtime/` source tree. It is not runnable: the client build copies the SDK out of it with `COPY --from`, installs it against its own interpreter, and puts the `sitecustomize` hook on `PYTHONPATH`. Python then starts the offload runtime before any application code imports, which is what lets `client.py` and `demo_model.py` stay free of SDK references.

```bash
CONTROLLER_INDEX_ARGS=()
RUNTIME_INDEX_ARGS=()
if [[ -n "${PYTHON_INDEX_URL:-}" ]]; then
  CONTROLLER_INDEX_ARGS=(--build-arg "PIP_INDEX_URL=$PYTHON_INDEX_URL")
  RUNTIME_INDEX_ARGS=(--build-arg "UV_INDEX_URL=$PYTHON_INDEX_URL")
fi

podman build \
  --file gpu-offload/controller/Containerfile \
  "${CONTROLLER_INDEX_ARGS[@]}" \
  --tag localhost/xavier-mutate:local \
  gpu-offload/controller

podman build \
  --file gpu-offload/runtime/Containerfile \
  --tag localhost/pyremote:local \
  gpu-offload/runtime

podman build \
  --file gpu-offload/examples/first-run/Containerfile \
  --target client \
  --build-arg REMOTER_IMAGE=localhost/pyremote:local \
  "${RUNTIME_INDEX_ARGS[@]}" \
  --tag localhost/gpu-offload-first-run:local \
  .

podman image exists localhost/xavier-mutate:local
podman image exists localhost/gpu-offload-first-run:local
```

`--target client` is required. The Containerfile also publishes a `gpu` stage that layers torch on the same image, and it is the last stage in the file, so an unnamed target builds the wrong one. The CPU path never needs that stage: the chart reuses the client image for the server.

The default builds use the package installers' public indexes. Set `PYTHON_INDEX_URL` when the network requires a credential-free Python package mirror:

```bash
export PYTHON_INDEX_URL=https://package-mirror.example.com/pypi/simple/
```

The controller build receives `PIP_INDEX_URL`; the runtime build receives `UV_INDEX_URL`. Do not include credentials because build arguments can appear in image metadata.

## Refs 41-42: Load Images into kind

Podman's image store and the kind node's containerd store are separate. Save and load each mutable local tag in its own archive:

```bash
podman save \
  --output /tmp/xavier-mutate-local.tar \
  localhost/xavier-mutate:local
podman save \
  --output /tmp/gpu-offload-first-run-local.tar \
  localhost/gpu-offload-first-run:local

kind load image-archive \
  /tmp/xavier-mutate-local.tar \
  --name "$GPU_OFFLOAD_CLUSTER"
kind load image-archive \
  /tmp/gpu-offload-first-run-local.tar \
  --name "$GPU_OFFLOAD_CLUSTER"
```

Verify both tags in the node:

```bash
podman exec "${GPU_OFFLOAD_CLUSTER}-control-plane" \
  ctr --namespace k8s.io images list | \
  grep -E 'localhost/(xavier-mutate|gpu-offload-first-run):local'
```

When rebuilding the same tag, remove it from the kind node before loading the replacement archive:

```bash
podman exec "${GPU_OFFLOAD_CLUSTER}-control-plane" \
  ctr --namespace k8s.io images remove \
  localhost/gpu-offload-first-run:local || true
```

## Refs 43-44: Install the Controller

Install the chart with the local controller image:

```bash
helm --kube-context "kind-${GPU_OFFLOAD_CLUSTER}" upgrade --install \
  gpu-offload gpu-offload/helm/gpu-offload \
  --namespace gpu-offload \
  --create-namespace \
  --set image.registry=localhost \
  --set mutate.image.repository=xavier-mutate \
  --set mutate.image.tag=local \
  --set image.pullPolicy=Never

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout status deployment/gpu-offload-mutate \
  --namespace gpu-offload \
  --timeout=120s
```

## Podman kind CPU Only

Use this path to verify admission, server generation, discovery, transport, and remote execution without allocating a GPU.

```bash
export GPU_OFFLOAD_CLUSTER=gpu-offload
kubectl config use-context "kind-${GPU_OFFLOAD_CLUSTER}"
```

### Ref 50: Set Up the CPU Stage

Install the runtime RBAC, offload configuration, and client Deployment. The image registry is a Helm value rather than part of the workload manifest:

```bash
helm --kube-context "kind-${GPU_OFFLOAD_CLUSTER}" upgrade --install \
  first-run gpu-offload/examples/first-run \
  --namespace gpu-offload-demo \
  --create-namespace \
  --set image.registry=localhost

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" wait --for=create \
  deployment/first-run-client-remote-server-cpu \
  --namespace gpu-offload-demo \
  --timeout=120s
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout status deployment/first-run-client-remote-server-cpu \
  --namespace gpu-offload-demo \
  --timeout=120s

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout restart deployment/first-run-client \
  --namespace gpu-offload-demo
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout status deployment/first-run-client \
  --namespace gpu-offload-demo \
  --timeout=120s
```

For an external registry, replace `localhost` and select a pull policy appropriate for that registry:

```bash
IMAGE_REGISTRY=example.azurecr.io

helm --kube-context "kind-${GPU_OFFLOAD_CLUSTER}" upgrade --install \
  first-run gpu-offload/examples/first-run \
  --namespace gpu-offload-demo \
  --create-namespace \
  --set image.registry="$IMAGE_REGISTRY" \
  --set image.pullPolicy=IfNotPresent
```

Set `image.repository`, `image.tag`, or `image.digest` when the external image does not use the defaults. For a private registry, create its Kubernetes pull secret in `gpu-offload-demo` and pass `--set imagePullSecrets[0].name=<secret-name>`.

The controller creates `first-run-client-remote-server-cpu`; the example chart does not declare it directly. Restarting the client after the server is ready avoids a first-call discovery race.

### Ref 51: Verify CPU Remote Execution

List the client and server pods, then read a result:

```bash
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" get pods --namespace gpu-offload-demo -o wide
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" logs deployment/first-run-client \
  --namespace gpu-offload-demo | \
  grep '"executed_by"' | tail -n 1
```

Successful output has this shape:

```json
{"executed_by": "first-run-client-remote-server-cpu-...", "predictions": [1, 4, 9, 16]}
```

The `executed_by` value must start with `first-run-client-remote-server-cpu`, not `first-run-client`. This proves that `demo_model.predict` ran in the remote server-stage pod.

## Podman kind NVIDIA on WSL2

Complete the [Podman kind NVIDIA setup](./01-local-kubernetes-setup.md#podman-kind-nvidia-on-wsl2) first. The node must advertise `nvidia.com/gpu: 1`, and the disposable GPU check must succeed.

```bash
export GPU_OFFLOAD_CLUSTER=gpu-offload-nvidia
kubectl config use-context "kind-${GPU_OFFLOAD_CLUSTER}"
```

### Ref 59: Build and Load the GPU Stage Image

The GPU stage runs a torch model, and the client image deliberately ships without torch. Build the `gpu` stage of the same Containerfile and load it into the node:

```bash
podman build \
  --file gpu-offload/examples/first-run/Containerfile \
  --target gpu \
  --build-arg REMOTER_IMAGE=localhost/pyremote:local \
  "${RUNTIME_INDEX_ARGS[@]}" \
  --tag localhost/gpu-offload-first-run-gpu:local \
  .

podman save \
  --output /tmp/gpu-offload-first-run-gpu-local.tar \
  localhost/gpu-offload-first-run-gpu:local
kind load image-archive \
  /tmp/gpu-offload-first-run-gpu-local.tar \
  --name "$GPU_OFFLOAD_CLUSTER"
```

Both images carry the same `sitecustomize` hook and the same application modules, so the offload seam resolves identically on each side of the call.

### Ref 60: Set Up the NVIDIA Stage

Install the shared example resources with the NVIDIA server-stage values. The client keeps the slim image; only the generated server moves to the torch image:

```bash
helm --kube-context "kind-${GPU_OFFLOAD_CLUSTER}" upgrade --install \
  first-run gpu-offload/examples/first-run \
  --namespace gpu-offload-demo \
  --create-namespace \
  --set image.registry=localhost \
  --set serverStage.name=nvidia \
  --set serverStage.gpu.enabled=true \
  --set serverStage.gpu.platform=wsl-nvidia \
  --set serverStage.image.repository=gpu-offload-first-run-gpu \
  --set serverStage.resources.requests.cpu=500m \
  --set serverStage.resources.requests.memory=2Gi \
  --set serverStage.resources.limits.cpu=4 \
  --set serverStage.resources.limits.memory=8Gi

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" wait --for=create \
  deployment/first-run-client-remote-server-nvidia \
  --namespace gpu-offload-demo \
  --timeout=120s
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout status deployment/first-run-client-remote-server-nvidia \
  --namespace gpu-offload-demo \
  --timeout=120s
```

When switching an existing release from the CPU profile, remove its obsolete generated Deployment. Then restart the client after the NVIDIA server is ready:

```bash
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" delete deployment first-run-client-remote-server-cpu \
  --namespace gpu-offload-demo \
  --ignore-not-found

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout restart deployment/first-run-client \
  --namespace gpu-offload-demo
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" rollout status deployment/first-run-client \
  --namespace gpu-offload-demo \
  --timeout=120s
```

### Ref 61: Verify GPU Allocation

Confirm that the generated server, not the client, requests one GPU:

```bash
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" get deployment first-run-client-remote-server-nvidia \
  --namespace gpu-offload-demo \
  --output jsonpath='{.spec.template.spec.containers[0].resources.limits.nvidia\.com/gpu}{"\n"}'

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" get deployment first-run-client \
  --namespace gpu-offload-demo \
  --output jsonpath='{.spec.template.spec.containers[0].resources.limits.nvidia\.com/gpu}{"\n"}'
```

The first command must print `1`. The second command must print an empty line.

Capture the generated server pod and verify its allocated WSL device and NVIDIA driver:

```bash
SERVER_POD=$(kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" get pods \
  --namespace gpu-offload-demo \
  --selector app=first-run-client-remote-server-nvidia \
  --output jsonpath='{.items[0].metadata.name}')

kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" exec "$SERVER_POD" \
  --namespace gpu-offload-demo \
  -- sh -c 'test -c /dev/dxg && echo DEVICE=/dev/dxg; /usr/lib/wsl/lib/nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader'
```

The command must print `DEVICE=/dev/dxg` and the NVIDIA adapter details.

### Ref 62: Verify NVIDIA Remote Execution

Read one client result:

```bash
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" logs deployment/first-run-client \
  --namespace gpu-offload-demo | \
  grep '"executed_by"' | tail -n 1
```

Successful output has this shape:

```json
{"executed_by": "first-run-client-remote-server-nvidia-...", "predictions": [1, 4, 9, 16]}
```

The `executed_by` hostname must match `$SERVER_POD`. Together with the server Deployment's GPU limit and the `nvidia-smi` result from that pod, this proves remote execution in the NVIDIA-allocated stage.

## Refs 90-91: Cleanup

Remove the example and controller:

```bash
helm --kube-context "kind-${GPU_OFFLOAD_CLUSTER}" uninstall first-run --namespace gpu-offload-demo
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" delete namespace gpu-offload-demo
helm --kube-context "kind-${GPU_OFFLOAD_CLUSTER}" uninstall gpu-offload --namespace gpu-offload
kubectl --context "kind-${GPU_OFFLOAD_CLUSTER}" delete namespace gpu-offload
rm -f \
  /tmp/xavier-mutate-local.tar \
  /tmp/gpu-offload-first-run-local.tar \
  /tmp/gpu-offload-first-run-gpu-local.tar
```

Delete the cluster with the cleanup command in [Local Podman and kind Setup](./01-local-kubernetes-setup.md#cleanup).

## Next Step

Replace `demo_model.predict` with model inference after this example succeeds. Build the model and runtime into the shared image, preserve the GPU limit on the NVIDIA server stage, and update `remotefuncs` to name the model function.
