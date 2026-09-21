---
title: GPU Offload Local Guides
description: Set up a local cluster and verify CPU, WSL NVIDIA, or bare-metal NVIDIA GPU offload
ms.date: 2026-08-28
ms.topic: get-started
---

Build the GPU-offload components and run a complete remote function call on a local cluster. Three platforms are supported, and every guide resolves the platform the same way [`scripts/detect-platform.sh`](../scripts/detect-platform.sh) does.

## 🚀 Start Here

Run the automated path from `gpu-offload/`:

```bash
mise trust
mise run a-detect
mise run f-10-setup
mise run f-20-verify
```

`a-detect` reports the resolved platform, cluster runtime, and cluster names. `f-10-setup` creates the cluster, builds and loads the images, installs the admission controller, and deploys the [first-run example](../examples/first-run/README.md). `f-20-verify` runs every check the platform supports.

To understand what those tasks do, or to work through the sequence by hand:

1. [Set up the local cluster](./01-local-kubernetes-setup.md) for your platform.
2. [Run the first local offload](./02-first-local-offload.md) and verify remote execution.

## 📋 Path Selection

| Platform           | Cluster runtime | Hardware                                       | Verification                                                                   |
|--------------------|-----------------|------------------------------------------------|--------------------------------------------------------------------------------|
| `cpu`              | kind            | Any supported Linux or WSL2 host               | Remote execution in a CPU server-stage pod                                     |
| `wsl-nvidia`       | kind            | NVIDIA GPU exposed to WSL2 through `/dev/dxg`  | Remote execution in a pod allocated one `nvidia.com/gpu`, plus a GPU model run |
| `baremetal-nvidia` | k3s             | NVIDIA GPU with the standard container toolkit | The same, through the NVIDIA device plugin rather than `/dev/dxg`              |

Every path validates image building, admission mutation, server-stage creation, peer discovery, transport, and remote execution. The GPU paths additionally verify CDI or device-plugin allocation, Kubernetes GPU capacity, that only the generated server holds a GPU, and that a torch model executed on the device while the client saw none.

Detection is automatic. Copy [`.env.example`](../.env.example) to `.env` to force `GPU_OFFLOAD_PLATFORM` or `GPU_OFFLOAD_RUNTIME`.

> [!NOTE]
> The WSL2 NVIDIA path needs local runtime configuration that is not suitable for
> production clusters. It registers `/dev/dxg` through a privileged generic device
> plugin and makes the WSL driver tree visible node-wide. See
> [the work items](./TODO.md) for the hardening work this implies.

## 🗺️ Diagrams

Mermaid sources. Render them with the VS Code Mermaid preview or the Mermaid CLI:

| Diagram                                                                  | Shows                                                                        |
|--------------------------------------------------------------------------|------------------------------------------------------------------------------|
| [remote-cluster-architecture.mmd](./remote-cluster-architecture.mmd)     | Robot site, cluster topology, and where each `gpu-offload/` artifact lands   |
| [deployment-admission-sequence.mmd](./deployment-admission-sequence.mmd) | Helm install through webhook mutation to a generated server Deployment       |
| [runtime-offload-sequence.mmd](./runtime-offload-sequence.mmd)           | One offloaded call from the robot through the client SDK to the server stage |

## 🧭 T0 Status

These guides provide the optional single-laptop Kubernetes substrate allowed at T0,
but they do not close the full robot lifecycle or validate the SO-101 path. See the
[GPU Offload T0 Plan](./05-T0-plan.md) for the gap analysis, required integration
changes, delivery sequence, and acceptance criteria.

## 📁 Contents

| Document                                                       | Purpose                                                            |
|----------------------------------------------------------------|--------------------------------------------------------------------|
| [01-local-kubernetes-setup.md](./01-local-kubernetes-setup.md) | Host packages, cluster creation, GPU registration, troubleshooting |
| [02-first-local-offload.md](./02-first-local-offload.md)       | Build, deploy, and verify the first-run example by hand            |
| [05-T0-plan.md](./05-T0-plan.md)                               | Gap analysis and delivery sequence for the T0 robot target         |
| [Work items](./TODO.md)                                        | Identified but incomplete implementation work                      |
