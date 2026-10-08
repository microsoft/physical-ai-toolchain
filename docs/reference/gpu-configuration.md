---
title: GPU Configuration
sidebar_label: GPU Configuration
sidebar_position: 1
description: GPU driver and operator configuration for H100 and RTX PRO 6000 nodes.
author: Microsoft Robotics-AI Team
ms.date: 2026-10-08
ms.topic: concept
---

GPU driver management, MIG configuration, and runtime behavior for the mixed GPU node pools used in this reference architecture.

## GPU Node Pool Architecture

This cluster uses two GPU node pool types with different driver and runtime profiles.

| Property               | H100 (`h100gpu`)               | RTX PRO 6000 (`rtxprogpu`)                                    |
|------------------------|--------------------------------|---------------------------------------------------------------|
| Azure VM SKU           | `Standard_NC40ads_H100_v5`     | `Standard_NC144ds_xl_RTXPRO6000BSE_v6` (one 96 GB GPU)        |
| GPU passthrough        | PCIe passthrough               | SR-IOV vGPU (PCI ID `10de:2bb5`)                              |
| Driver source          | GPU Operator datacenter driver | AKS-managed GRID driver (`gpu_driver = "Install"`)            |
| Driver branch          | Standard datacenter            | Microsoft GRID vGPU 20 (R595)                                 |
| MIG at hardware level  | Disabled                       | Enabled by vGPU host                                          |
| Vulkan device creation | Supported                      | Supported (requires `NVIDIA_DRIVER_CAPABILITIES=all`)         |
| Kernel module type     | Open (default)                 | Open (Microsoft installs the NCv6 GRID driver with `-M open`) |

### RTX PRO 6000 Sizes and Quota

Use a GA size. The preview sizes with 128, 256, and 320 vCPUs no longer deploy, so pools created on them fail to allocate nodes and must move to a new pool on a GA size.

| Size                                   | vCPUs | GPUs | GPU memory |
|----------------------------------------|-------|------|------------|
| `Standard_NC36ds_xl_RTXPRO6000BSE_v6`  | 36    | 1/4  | 24 GB      |
| `Standard_NC72ds_xl_RTXPRO6000BSE_v6`  | 72    | 1/2  | 48 GB      |
| `Standard_NC144ds_xl_RTXPRO6000BSE_v6` | 144   | 1    | 96 GB      |
| `Standard_NC288ds_xl_RTXPRO6000BSE_v6` | 288   | 2    | 192 GB     |

Every node counts its vCPUs against the regional RTX PRO 6000 quota family, and an upgrade surges one extra node by default. A one-node `NC144ds_xl` pool needs 288 vCPUs of quota to upgrade, or 144 when the pool sets `max_unavailable = "1"` and accepts downtime while its node upgrades.

Until the quota exists, keep the pool parked with autoscaling off and `node_count = 0`, as described in [Manage Node Pools](../infrastructure/manage-node-pools.md#park-a-pool-without-nodes), so no deployment or scale-up tries to allocate a node.

## GPU Driver Management

### H100 Nodes

The GPU Operator manages the full driver lifecycle for H100 nodes using its built-in driver container (`driver.enabled: true`). No additional configuration is required.

### RTX PRO 6000 Nodes

RTX PRO 6000 BSE nodes use Azure SR-IOV vGPU passthrough, which needs the Microsoft GRID driver instead of the NVIDIA datacenter driver. AKS installs the GRID driver (vGPU 20, R595) itself when the pool sets `gpu_driver = "Install"`, so new pools need no custom installer or driver label. The GPU Operator detects the AKS-installed driver, labels the nodes `nvidia.com/gpu.deploy.driver=pre-installed`, and skips its driver container while still managing the toolkit, device-plugin, and validator components.

#### GRID Driver DaemonSet Fallback

A pool created with `gpu_driver = "None"` gets no AKS driver. For those pools, the `gpu-grid-driver-installer` DaemonSet ([manifests/gpu-grid-driver-installer.yaml](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/setup/manifests/gpu-grid-driver-installer.yaml)) installs Microsoft's vGPU 20.2 GRID driver on each node labeled `nvidia.com/gpu.deploy.driver=false`. The label also makes the GPU Operator skip its driver DaemonSet on those nodes.

`01-deploy-robotics-charts.sh` applies the DaemonSet only when labeled nodes exist. A pool's driver choice can't change after creation, so moving a fallback pool to AKS-managed drivers means creating a new pool.

The GRID driver is installed via an init container that uses `nsenter` into the host namespace to download, verify, and compile the driver. New nodes added by the autoscaler receive the driver automatically through the DaemonSet.

#### GPU Operator Validation Dependency

This dependency applies to nodes that use the fallback DaemonSet. The GPU Operator's downstream components (toolkit, device-plugin, GFD, DCGM exporter, validator) each have a `driver-validation` init container that performs two checks before allowing the main container to start:

1. **Validation marker**: Polls for `/run/nvidia/validations/.driver-ctr-ready` on the host. On operator-managed nodes, the driver DaemonSet creates this file. On nodes with a pre-installed driver (`nvidia.com/gpu.deploy.driver=false`), the GRID driver installer creates it.

2. **Driver root**: Validates the driver installation by looking for binaries and libraries under `/run/nvidia/driver/` — the path where the GPU Operator's driver container normally bind-mounts its rootfs. For pre-installed drivers, the GRID driver installer bind-mounts the host root (`/`) to `/run/nvidia/driver/` so the validator finds the system-installed driver at the expected paths.

Without both of these, all downstream GPU Operator pods remain stuck in `Init:0/1` indefinitely.

> [!NOTE]
> The GPU Operator supports [custom vGPU driver containers](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/install-gpu-operator-vgpu.html),
> but this requires building a private container image, a private registry, and NVIDIA vGPU licensing infrastructure.
> The DaemonSet approach is functionally equivalent without that overhead.

### Node OS on Kubernetes 1.35

Kubernetes 1.35 moves node pools that use the default `Ubuntu` OS SKU to Ubuntu 24.04 and containerd 2.3. On that image, the GPU Operator v25.10 container toolkit writes `/etc/containerd/conf.d/99-nvidia.toml` with `version = 4` next to AKS's `version = 2` root config, and containerd refuses to start:

```text
containerd: drop-in config version 4 higher than root config version 2
```

The node then stays `NotReady` with `ContainerRuntimeProblem` and never advertises a GPU. The toolkit runs on every GPU node, including pools with AKS-installed drivers. Set `os_sku = "Ubuntu2204"` on GPU pools until the GPU Operator release you deploy handles this layout. AKS supports `Ubuntu2204` through Kubernetes 1.36, and changing `os_sku` between Ubuntu versions updates a pool in place. CPU pools can run Ubuntu 24.04.

### Driver Auto-upgrade on AKS-installed Drivers

The GPU Operator chart enables driver auto-upgrade (`driver.upgradePolicy.autoUpgrade: true`). On a new node with an AKS-installed driver, the upgrade controller can cordon the node and leave it at `nvidia.com/gpu-driver-upgrade-state=pod-restart-required`, waiting for a driver pod that never returns because the node is labeled `nvidia.com/gpu.deploy.driver=pre-installed`. Work that scaled the pool up can't schedule on it. Label pools that set `gpu_driver = "Install"` so the controller skips them:

```hcl
node_labels = { "nvidia.com/gpu-driver-upgrade.skip" = "true" }
```

AKS updates those drivers through node image upgrades, not through the GPU Operator.

## MIG Strategy

The GPU Operator `mig.strategy` setting controls how GPUs are exposed to workload containers.

### Why `single` Is Required

The Azure vGPU host enables MIG mode on RTX PRO 6000 GPUs. When MIG is enabled at the hardware level, CUDA can only access the GPU through MIG device UUIDs, not bare GPU UUIDs.

| Strategy | Device-plugin sets `NVIDIA_VISIBLE_DEVICES` to | CUDA result                                    |
|----------|------------------------------------------------|------------------------------------------------|
| `single` | `MIG-<uuid>` (MIG device UUID)                 | Works                                          |
| `none`   | `GPU-<uuid>` (bare GPU UUID)                   | Fails: `cuInit` returns `CUDA_ERROR_NO_DEVICE` |

With `strategy: none`, `nvidia-smi` works (it uses NVML, which is MIG-agnostic) but PyTorch/CUDA applications cannot initialize the GPU.

### Guest VM MIG Limitations

The guest VM cannot create, destroy, or reconfigure MIG instances:

```text
nvidia-smi -i 0 -mig 0 → Unable to disable MIG Mode: Insufficient Permissions
nvidia-smi mig -lgi    → Insufficient Permissions
```

The vGPU host manages MIG instances, so `migManager.enabled` is set to `false` in the GPU Operator values.

### H100 Compatibility

H100 nodes do not have MIG enabled at hardware level. Setting `mig.strategy: single` on a non-MIG GPU is a no-op — the device-plugin falls back to standard GPU UUID allocation. H100 workloads are unaffected.

## Container GPU Capabilities

The NVIDIA Container Runtime controls which GPU libraries and APIs are available inside containers through the `NVIDIA_DRIVER_CAPABILITIES` environment variable. The default value is `utility,compute`, which provides only CUDA and `nvidia-smi`.

Isaac Sim requires Vulkan for its rendering subsystem. Without Vulkan, `vkCreateDevice` fails during startup and `simulation_app.close()` hangs indefinitely during shutdown. Training itself completes (PhysX runs on CUDA), but the process never exits.

All OSMO workflow templates must set:

```yaml
environment:
  NVIDIA_DRIVER_CAPABILITIES: "all"
```

| Capability | APIs provided                       | Required by                      |
|------------|-------------------------------------|----------------------------------|
| `compute`  | CUDA, OpenCL                        | All GPU workloads                |
| `utility`  | `nvidia-smi`, NVML                  | Monitoring                       |
| `graphics` | Vulkan, OpenGL                      | Isaac Sim rendering and shutdown |
| `all`      | All of the above plus video/display | Recommended for simplicity       |

### RTX PRO 6000 vGPU Profile

A whole-GPU RTX PRO 6000 VM exposes a `DC-4-96Q` vGPU profile (Q-series = RTX Virtual Workstation). Q-series profiles provide full Vulkan support including ray tracing extensions. This output was captured on a whole-GPU node running the earlier 580 GRID driver:

```text
$ vulkaninfo --summary
GPU0:
    apiVersion    = 1.4.312
    deviceName    = NVIDIA RTX Pro 6000 Blackwell DC-4-96Q
    driverVersion = 580.105.8.0

$ vulkaninfo | grep ray_tracing
    VK_KHR_ray_tracing_pipeline    : extension revision 1
    VK_KHR_acceleration_structure  : extension revision 13
```

When `NVIDIA_DRIVER_CAPABILITIES` omits `graphics`, the container toolkit does not mount the Vulkan ICD (`nvidia_icd.json`) or the graphics shared libraries (`libGLX_nvidia.so.0`). Isaac Sim detects the GPU via NVML but fails at Vulkan device creation:

```text
[Error] [carb.graphics-vulkan.plugin] VkResult: ERROR_INITIALIZATION_FAILED
[Error] [carb.graphics-vulkan.plugin] vkCreateDevice failed.
[Error] [gpu.foundation.plugin] No device could be created.
```

### Reference

* [NVIDIA Container Toolkit specialized configurations](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html)
* [Isaac Sim container installation](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html)
* [Azure NC RTX PRO 6000 BSE v6 series](https://learn.microsoft.com/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series)

## Isaac Sim 4.x Shutdown Fix

[`DirectRLEnv.close()`](https://github.com/isaac-sim/IsaacLab/blob/main/source/isaaclab/isaaclab/envs/direct_rl_env.py#L503-L530) and [`ManagerBasedEnv.close()`](https://github.com/isaac-sim/IsaacLab/blob/main/source/isaaclab/isaaclab/envs/manager_based_env.py#L523-L550) both guard `sim.stop()` behind a version and feature check:

```python
if get_isaac_sim_version().major >= 5:
    if self.cfg.sim.create_stage_in_memory:
        omni.physx.get_physx_simulation_interface().detach_stage()
        self.sim.stop()
        self.sim.clear()
```

The version guard exists because `create_stage_in_memory` is an Isaac Sim 5.0+ optimization — the detach/stop/clear sequence was added to clean up in-memory stages, not as a general shutdown fix. The individual APIs (`sim.stop()`, `sim.clear()`, `detach_stage()`) exist on Isaac Sim 4.x but are never called during `close()` on that version.

On Isaac Sim 4.x, `sim.stop()` never executes. The timeline remains playing when `simulation_app.close()` triggers Kit shutdown. Kit's shutdown fires a `TimelineEventType.STOP` event, which invokes [`_app_control_on_stop_handle_fn`](https://github.com/isaac-sim/IsaacLab/blob/main/source/isaaclab/isaaclab/sim/simulation_context.py#L1010-L1030):

```python
def _app_control_on_stop_handle_fn(self, event):
    if not self._disable_app_control_on_stop_handle:
        while not omni.timeline.get_timeline_interface().is_playing():
            self.render()
```

This callback enters an infinite render loop because the timeline was just stopped (not playing) and nothing restarts it. The loop runs in C++ and never yields to the Python interpreter, preventing signal-based timeouts from functioning.

All training scripts call `prepare_for_shutdown()` from [`simulation_shutdown.py`](https://github.com/microsoft/physical-ai-toolchain/blob/main/training/rl/simulation_shutdown.py) before `env.close()`. This function neutralizes the callback:

1. Sets [`_disable_app_control_on_stop_handle`](https://github.com/isaac-sim/IsaacLab/blob/main/source/isaaclab/isaaclab/sim/simulation_context.py#L257) to `True` as a first layer of defense.
2. Unsubscribes the `_app_control_on_stop_handle` callback entirely, removing it from the timeline event stream so it cannot fire during Kit shutdown.
3. Forks a watchdog process that sends `SIGKILL` after 30 seconds as a safety net. A forked process is required because neither Python threads nor `SIGALRM` signal handlers can execute while native C code holds the GIL.

`detach_stage()` and `sim.stop()` are intentionally omitted. On vGPU nodes where Vulkan initialization fails (`vkCreateDevice` returns `ERROR_INITIALIZATION_FAILED`), both calls block indefinitely in native C code while holding the GIL. `detach_stage()` targets in-memory stages introduced in Isaac Sim 5.0 and has no effect on standard USD stages. `sim.stop()` triggers the timeline STOP event, which enters the Omniverse event dispatch layer and never returns when the rendering subsystem is degraded.

After `env.close()`, training scripts call `os._exit(0)` instead of `simulation_app.close()`. Kit's native shutdown sequence also hangs on vGPU nodes with failed Vulkan initialization, and in a Kubernetes training pod the container runtime handles resource cleanup.

## Related Resources

* [Use GPUs on AKS](https://learn.microsoft.com/azure/aks/use-nvidia-gpu)
* [Install NVIDIA GPU drivers on N-series VMs running Linux](https://learn.microsoft.com/azure/virtual-machines/linux/n-series-driver-setup)
* [NVIDIA GPU Operator with Azure AKS](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/microsoft-aks.html)
* [NVIDIA GPU Operator vGPU support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/install-gpu-operator-vgpu.html)
* [GPU Operator Helm values](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/setup/values/nvidia-gpu-operator.yaml)
* [GRID driver installer DaemonSet](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/setup/manifests/gpu-grid-driver-installer.yaml)

---

🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.
