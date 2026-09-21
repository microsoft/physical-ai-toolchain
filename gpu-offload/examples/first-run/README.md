# first-run: the offload environment check

<!-- cspell:ignore nvidiactl pyremote matmul remoter -->

Proves that a machine can run the offload stack end to end, before any real workload or
robot is involved. The client squares four integers and, on GPU platforms, runs a small
convolutional model. Both calls execute in a generated server-stage pod, and the client
container never holds a GPU.

This is the example the setup tasks and the
[docs walkthrough](../../docs/02-first-local-offload.md) drive. Run it first on any new
host.

## 🧭 What This Example Demonstrates

Two things at once, and both matter.

First, that the environment works: the cluster admits the workload, the controller
generates the server Deployment, the transport connects, and on GPU platforms the device
plugin hands the stage a real GPU.

Second, that the offload is transparent. No file in this example imports the remoter SDK.
`client.py`, `demo_model.py`, and `gpu_model.py` are ordinary modules; the image is what
makes their calls remote. That is the same layering
[`ur10e-single`](../ur10e-single/README.md) uses for an unmodified robot deployment, at a
size that fits on one screen.

| Layer            | Source                                                           | Role                                                         |
|------------------|------------------------------------------------------------------|--------------------------------------------------------------|
| Remoter SDK      | [`runtime/`](../../runtime) published as a payload image         | Copied in with `COPY --from`, installed into the client venv |
| Startup hook     | `remoter/sitecustomizer.py` on `PYTHONPATH`                      | Starts the offload runtime at interpreter start              |
| Offload boundary | [demo_model.py](./demo_model.py), [gpu_model.py](./gpu_model.py) | The functions `remote.yaml` moves to the server stage        |
| Opt-in signals   | [templates/manifests.yaml.tpl](./templates/manifests.yaml.tpl)   | `xavier: "true"` label and `xavierconfig` annotation         |

Everything else is injected at admission: the `remote.yaml` mount, `REMOTER_CONFIG`,
`SERVERLABEL`, and the generated server Deployment.

> [!NOTE]
> The generated server runs `python3 -m remoter.autoremote` with `SERVER=true`, which is
> the condition `sitecustomizer.py` checks before auto-starting. The same image is
> therefore safe on both sides of the call.

## 🧩 The Offload Boundary

[remote.yaml](./remote.yaml) is the readable reference spec. The chart renders the same
structure into a ConfigMap with the stage name, server image, and GPU resources filled in
from [values.yaml](./values.yaml).

| Entry                      | Platforms | Effect                                                           |
|----------------------------|-----------|------------------------------------------------------------------|
| `demo_model//predict`      | All       | Squares four integers and returns the executing pod's `HOSTNAME` |
| `gpu_model//gpu_inference` | GPU only  | Runs a seeded convolution and a 1024x1024 matmul on the device   |

The empty class segment in `demo_model//predict` is required. Dehydration keys are
`"<module>/<class>/<attr>"`, and a module-level function has no class.

`gpu_inference` returns plain Python types only, because the MessagePack codec rejects
tensors. It also runs the identical seeded model on the CPU and reports
`max_abs_diff_vs_cpu`, so the GPU result is confirmed to be numerically correct rather
than noise.

## 🖥️ Platforms

[`scripts/detect-platform.sh`](../../scripts/detect-platform.sh) resolves the platform and
every task follows it. Override it in `.env`.

| Platform           | Detected by                       | Cluster | Stage    | Images                      |
|--------------------|-----------------------------------|---------|----------|-----------------------------|
| `cpu`              | Neither GPU probe matches         | kind    | `cpu`    | Client image only           |
| `wsl-nvidia`       | `/dev/dxg` and `/usr/lib/wsl`     | kind    | `nvidia` | Client plus GPU stage image |
| `baremetal-nvidia` | `/dev/nvidiactl` and `nvidia-smi` | k3s     | `nvidia` | Client plus GPU stage image |

On `cpu` the chart reuses the client image for the server stage and remotes only
`predict`. There is nothing to skip and no GPU section to work around: the CPU path is a
complete run of the offload stack.

WSL2 is supported on both paths. Without a GPU it resolves to `cpu` and needs no special
handling. With one, the stage reaches the device through `/dev/dxg` and the mounted
Windows driver tree, so the chart adds `LD_LIBRARY_PATH=/usr/lib/wsl/lib` to the server
container instead of relying on the standard NVIDIA container stack.

## 🚀 Running It

From `gpu-offload/`:

```bash
mise run f-10-setup
mise run f-20-verify
```

`f-10-setup` resolves the platform, creates the cluster, builds and loads the images,
installs the controller, and deploys this example. `f-20-verify` runs the checks:

| Task                                | Platforms | Asserts                                                 |
|-------------------------------------|-----------|---------------------------------------------------------|
| `d-offload-51-check-execution`      | All       | `executed_by` names the server pod, not the client      |
| `d-offload-61-check-gpu-allocation` | GPU only  | Only the generated server Deployment requests a GPU     |
| `d-offload-62-check-gpu-model`      | GPU only  | The model ran on CUDA and the client sees no GPU device |

Remove the workloads with `mise run f-30-teardown`, or the cluster as well with
`mise run f-40-teardown-all`.

## 🐳 Images

One Containerfile publishes two stages. The build target is never left to default,
because the GPU stage is last in the file.

| Stage    | Tag                                         | Contents                                   |
|----------|---------------------------------------------|--------------------------------------------|
| `client` | `localhost/gpu-offload-first-run:local`     | Python, the SDK, the hook, the app modules |
| `gpu`    | `localhost/gpu-offload-first-run-gpu:local` | The same, plus torch                       |

The client image has no torch and requests no GPU. That emptiness is the negative
control: `gpu_inference` reports a CUDA device only because it executed elsewhere.

> [!WARNING]
> Do not rebase these images on `nvidia/cuda`. Those images set
> `NVIDIA_VISIBLE_DEVICES=all`, which makes every container see the GPU once the NVIDIA
> runtime is the containerd default, and the client-sees-no-GPU check stops meaning
> anything. The torch wheels bundle the CUDA runtime, so only the host driver is needed.

Hosts that build through an internal package proxy set `UV_INDEX_URL` in `.env`; the
build script forwards it only when it is set. Do not put credentials in it, because build
arguments are recorded in image history.

## 📁 Files

| Path                                                           | Purpose                                             |
|----------------------------------------------------------------|-----------------------------------------------------|
| [Containerfile](./Containerfile)                               | Client and GPU stage images with the SDK layered in |
| [client.py](./client.py)                                       | Control-container entry point; no SDK references    |
| [demo_model.py](./demo_model.py)                               | The transport check                                 |
| [gpu_model.py](./gpu_model.py)                                 | The GPU check                                       |
| [remote.yaml](./remote.yaml)                                   | Reference offload spec                              |
| [templates/manifests.yaml.tpl](./templates/manifests.yaml.tpl) | ConfigMap, RBAC, and the client Deployment          |
| [values.yaml](./values.yaml)                                   | Image, stage, GPU, and resource settings            |
| [scripts/](./scripts)                                          | Build, load, deploy, check, and teardown            |

## ➡️ Next

This example carries no hardware, no checkpoint, and no real policy. Once it passes, see
[`ur10e-single`](../ur10e-single/README.md) for the same pattern applied to an unmodified
robot deployment with a 7 GB Pi0.5 checkpoint resident on the stage.
