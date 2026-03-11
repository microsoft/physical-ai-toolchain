---
title: GPU Configuration
sidebar_label: GPU Configuration
sidebar_position: 1
description: GPU driver and operator configuration for H100 and RTX PRO 6000 nodes.
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: concept
---

GPU driver management, MIG configuration, and runtime behavior for the mixed GPU node pools used in this reference architecture.

## GPU Node Pool Architecture

This cluster uses two GPU node pool types with different driver and runtime profiles.

| Property               | H100 (`h100gpu`)               | RTX PRO 6000 (`rtxprogpu`)                            |
|------------------------|--------------------------------|-------------------------------------------------------|
| Azure VM SKU           | `Standard_NC40ads_H100_v5`     | `Standard_NC128ds_xl_RTXPRO6000BSE_v6`                |
| GPU passthrough        | PCIe passthrough               | SR-IOV vGPU (PCI ID `10de:2bb5`)                      |
| Driver source          | GPU Operator datacenter driver | Custom GRID DaemonSet (`gpu-grid-driver-installer`)   |
| Driver branch          | Standard datacenter            | Microsoft GRID `580.105.08-grid-azure`                |
| MIG at hardware level  | Disabled                       | Enabled by vGPU host                                  |
| Vulkan device creation | Supported                      | Supported (requires `NVIDIA_DRIVER_CAPABILITIES=all`) |
| Kernel module type     | Open (default)                 | Proprietary (required for vGPU)                       |

## GPU Driver Management

### H100 Nodes

The GPU Operator manages the full driver lifecycle for H100 nodes using its built-in driver container (`driver.enabled: true`). No additional configuration is required.

### RTX PRO 6000 Nodes

RTX PRO 6000 BSE nodes use Azure SR-IOV vGPU passthrough, which requires the Microsoft GRID driver instead of the NVIDIA datacenter driver. AKS does not support `gpu_driver = "Install"` for this VM SKU.

The `gpu-grid-driver-installer` DaemonSet ([manifests/gpu-grid-driver-installer.yaml](../deploy/002-setup/manifests/gpu-grid-driver-installer.yaml)) installs the GRID driver on each RTX node. Terraform labels these nodes with `nvidia.com/gpu.deploy.driver=false`, causing the GPU Operator to skip its driver DaemonSet on those nodes while still managing toolkit, device-plugin, and validator components.

The GRID driver is installed via an init container that uses `nsenter` into the host namespace to download and compile the driver. New nodes added by the autoscaler receive the driver automatically through the DaemonSet.

#### GPU Operator Validation Dependency

The GPU Operator's downstream components (toolkit, device-plugin, GFD, DCGM exporter, validator) each have a `driver-validation` init container that performs two checks before allowing the main container to start:

1. **Validation marker**: Polls for `/run/nvidia/validations/.driver-ctr-ready` on the host. On operator-managed nodes, the driver DaemonSet creates this file. On nodes with a pre-installed driver (`nvidia.com/gpu.deploy.driver=false`), the GRID driver installer creates it.

2. **Driver root**: Validates the driver installation by looking for binaries and libraries under `/run/nvidia/driver/` — the path where the GPU Operator's driver container normally bind-mounts its rootfs. For pre-installed drivers, the GRID driver installer bind-mounts the host root (`/`) to `/run/nvidia/driver/` so the validator finds the system-installed driver at the expected paths.

Without both of these, all downstream GPU Operator pods remain stuck in `Init:0/1` indefinitely.

> [!NOTE]
> The GPU Operator supports [custom vGPU driver containers](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/install-gpu-operator-vgpu.html),
> but this requires building a private container image, a private registry, and NVIDIA vGPU licensing infrastructure.
> The DaemonSet approach is functionally equivalent without that overhead.

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

The `Standard_NC128ds_xl_RTXPRO6000BSE_v6` VM exposes a `DC-4-96Q` vGPU profile (Q-series = RTX Virtual Workstation). Q-series profiles provide full Vulkan support including ray tracing extensions:

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

All training scripts call `prepare_for_shutdown()` from [`simulation_shutdown.py`](../src/training/simulation_shutdown.py) before `env.close()`. This function neutralizes the callback:

1. Sets [`_disable_app_control_on_stop_handle`](https://github.com/isaac-sim/IsaacLab/blob/main/source/isaaclab/isaaclab/sim/simulation_context.py#L257) to `True` as a first layer of defense.
2. Unsubscribes the `_app_control_on_stop_handle` callback entirely, removing it from the timeline event stream so it cannot fire during Kit shutdown.
3. Forks a watchdog process that sends `SIGKILL` after 30 seconds as a safety net. A forked process is required because neither Python threads nor `SIGALRM` signal handlers can execute while native C code holds the GIL.

`detach_stage()` and `sim.stop()` are intentionally omitted. On vGPU nodes where Vulkan initialization fails (`vkCreateDevice` returns `ERROR_INITIALIZATION_FAILED`), both calls block indefinitely in native C code while holding the GIL. `detach_stage()` targets in-memory stages introduced in Isaac Sim 5.0 and has no effect on standard USD stages. `sim.stop()` triggers the timeline STOP event, which enters the Omniverse event dispatch layer and never returns when the rendering subsystem is degraded.

After `env.close()`, training scripts call `os._exit(0)` instead of `simulation_app.close()`. Kit's native shutdown sequence also hangs on vGPU nodes with failed Vulkan initialization, and in a Kubernetes training pod the container runtime handles resource cleanup.

## Related Resources

* [NVIDIA GPU Operator with Azure AKS](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/microsoft-aks.html)
* [NVIDIA GPU Operator vGPU support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/install-gpu-operator-vgpu.html)
* [GPU Operator Helm values](../deploy/002-setup/values/nvidia-gpu-operator.yaml)
* [GRID driver installer DaemonSet](../deploy/002-setup/manifests/gpu-grid-driver-installer.yaml)
