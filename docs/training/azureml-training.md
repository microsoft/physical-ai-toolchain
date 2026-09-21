---
sidebar_position: 2
title: Azure ML Training Workflows
description: Submit Isaac Lab and LeRobot training jobs to Azure Machine Learning
author: Microsoft Robotics-AI Team
ms.date: 2026-09-19
ms.topic: how-to
keywords:
  - azure ml
  - training
  - isaac lab
  - lerobot
---

Submit Isaac Lab reinforcement learning and LeRobot behavioral cloning training jobs to Azure Machine Learning using Kubernetes compute targets.

## 📋 Prerequisites

| Component          | Requirement                                                    |
|--------------------|----------------------------------------------------------------|
| AzureML extension  | Deployed via `02-deploy-azureml-extension.sh`                  |
| Kubernetes compute | GPU-capable compute target attached to AzureML workspace       |
| Azure subscription | Subscription ID, resource group, and workspace name configured |

## 📦 Available Templates

Selected RL, LeRobot, and software-in-the-loop (SiL) examples, not an exhaustive inventory of training families. See [Workflow Templates (AzureML)](../reference/workflow-templates-azureml.md) for source paths and pipeline templates. Submission scripts supply runtime commands and environment-specific values; structural YAML defaults are not standalone deployment instructions.

| Template                   | Purpose                    | Submission Script                                              |
|----------------------------|----------------------------|----------------------------------------------------------------|
| `train.yaml`               | Isaac Lab SKRL training    | `training/rl/scripts/submit-azureml-training.sh`               |
| `isaaclab-evaluation.yaml` | Isaac Lab evaluation       | `evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh` |
| `lerobot-train.yaml`       | LeRobot behavioral cloning | `training/il/scripts/submit-azureml-lerobot-training.sh`       |

## ⚙️ Isaac Lab Training Parameters

| Parameter         | Description                                                 |
|-------------------|-------------------------------------------------------------|
| `mode`            | Execution mode: `train` (default) or `smoke-test`           |
| `checkpoint_mode` | Checkpoint strategy: `from-scratch`, `warm-start`, `resume` |
| `task`            | Isaac Lab task name (e.g., `Isaac-Cartpole-v0`)             |
| `num_envs`        | Number of parallel environments                             |
| `headless`        | Run without rendering (default: `true`)                     |
| `max_iterations`  | Maximum training iterations                                 |

Continue training with `checkpoint_mode` and a checkpoint URI; `retrain` is not an execution mode.

## 🤖 LeRobot Training Parameters

| Parameter         | Default                       | Description                                      |
|-------------------|-------------------------------|--------------------------------------------------|
| `dataset_repo_id` | (required)                    | HuggingFace dataset repository                   |
| `policy_type`     | `act`                         | Policy architecture: `act`, `diffusion`          |
| `job_name`        | `lerobot-act-training`        | Unique job identifier                            |
| `image`           | `DEFAULT_LEROBOT_TRAIN_IMAGE` | Digest-pinned default in `scripts/lib/common.sh` |
| `save_freq`       | `5000`                        | Checkpoint save frequency                        |
| `instance_type`   | `gpuspot`                     | Pod size (AzureML-on-Kubernetes only)            |
| `mixed_precision` | `no`                          | Accelerate mixed precision (no/fp16/bf16)        |

### Single-node multi-GPU training

LeRobot training on Azure ML supports single-node multi-GPU execution via [Hugging Face Accelerate](https://huggingface.co/docs/lerobot/multi_gpu_training). The wrapper detects the visible GPU count at runtime via `torch.cuda.device_count()` and, when `N > 1`, automatically launches `accelerate launch --multi_gpu --num_processes=N`. No AzureML `distribution:` block is required because the run stays within one process group on one node.

Both AzureML compute backends are supported. GPU count is determined by the backend:

- **AzureML managed compute (`AmlCompute`):** GPU count visible to the job container equals the cluster's VM SKU GPU count (e.g., `Standard_NC48ads_A100_v4` → 2, `Standard_NC96ads_A100_v4` → 4). Pass `--compute <cluster-name>` (matching an entry in `aml_compute_clusters`).
- **AzureML-on-Kubernetes (Arc-attached AKS):** GPU count visible to the job container is the `InstanceType` CRD's `nvidia.com/gpu: N` request. `gpu2`/`gpuspot2`/`gpu4`/`gpuspot4` are shipped in `infrastructure/setup/manifests/azureml-instance-types.yaml` and require a node SKU with at least `N` GPUs (e.g., `Standard_NC128ds_xl_RTXPRO6000BSE_v6` for `N=4`).

Managed compute example:

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --dataset-repo-id user/dataset \
  --compute gpu-training \
  --mixed-precision bf16 \
  --batch-size 8
```

AzureML-on-Kubernetes example:

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --dataset-repo-id user/dataset \
  --instance-type gpu4 \
  --mixed-precision bf16 \
  --batch-size 8
```

> [!NOTE]
> LeRobot does NOT auto-scale the learning rate or training steps with GPU count. The effective batch size is `batch_size × num_gpus` (logged to MLflow as `effective_batch_size`); adjust `--training-steps` for the intended training budget. The AzureML submission script does not expose a `--learning-rate` option. The `--policy.use_amp` flag is ignored under Accelerate and is stripped by the wrapper with a warning.

## 🔧 Environment Variables

| Variable                 | Description                    |
|--------------------------|--------------------------------|
| `AZURE_SUBSCRIPTION_ID`  | Azure subscription ID          |
| `AZURE_RESOURCE_GROUP`   | Resource group name            |
| `AZUREML_WORKSPACE_NAME` | Azure ML workspace name        |
| `AZUREML_COMPUTE`        | Kubernetes compute target name |

Scripts auto-detect these values from Terraform outputs. Override using CLI arguments or environment variables.

## 🚀 Quick Start

Isaac Lab SKRL training:

```bash
# Default configuration from Terraform outputs
./training/rl/scripts/submit-azureml-training.sh

# Custom task and environment count
./training/rl/scripts/submit-azureml-training.sh \
  --task Isaac-Cartpole-v0 \
  --num-envs 512 \
  --max-iterations 1000
```

Isaac Lab evaluation:

```bash
./evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh \
  --task Isaac-Cartpole-v0 \
  --model-name cartpole-policy \
  --model-version 1
```

LeRobot training:

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --dataset-repo-id lerobot/aloha_sim_insertion_human \
  --policy-type act
```

## 💾 Checkpoint Management

| Mode           | Behavior                                   |
|----------------|--------------------------------------------|
| `from-scratch` | Start training from random initialization  |
| `warm-start`   | Load weights; reset optimizer and counters |
| `resume`       | Restore training state from a checkpoint   |

`fresh` is an alias for `from-scratch`. For `warm-start` or `resume`, supply a checkpoint URI as well as `--checkpoint-mode`:

```bash
./training/rl/scripts/submit-azureml-training.sh \
  --checkpoint-mode resume \
  --checkpoint-uri "models:/cartpole-policy/1" \
  --task Isaac-Cartpole-v0
```

## 🛌 Scale-from-zero GPU Pools

The SiL module's default GPU pool uses `min_count = 0`; the root deployment's default GPU pool uses `min_count = 1`. Check the effective `node_pools` configuration before assuming scale-to-zero. For a pool configured to reach zero, the setup script overrides two scheduler checks and the Terraform pool definition supplies a static GPU label, as described below.

### `aml-operator` resource validation

The Azure ML Kubernetes extension installs `aml-operator`, which runs a pre-flight check on every submitted `AmlJob`:

> Does the requested `InstanceType` fit inside the largest currently-Ready node?

With the chart default `amloperator.skipResourceValidation: false`, the operator fails the job immediately with `Code: 9` ("Invalid instance type. The instance type defined resource requirement has exceeded the node size") whenever the target GPU pool is at zero. No Pod is created, kube-scheduler is never invoked, and the cluster autoscaler never observes a pending Pod to scale up against.

Result: a permanent deadlock — you cannot submit the job that would cause the GPU resource to become available.

`02-deploy-azureml-extension.sh` sets the flag to `true` by default. Override with `--enforce-resource-validation` on fixed-capacity clusters where you want misconfigured InstanceTypes to fail fast at submission rather than producing Pods stuck in `Pending`.

Trade-off when enabled (the default): a typo in an `InstanceType` (e.g. `nvidia.com/gpu: 8` on a 4-GPU SKU) manifests as `FailedScheduling` events on a long-Pending Pod instead of an immediate job failure. Diagnose with `kubectl describe pod`.

### Static `accelerator=nvidia` node label

The InstanceTypes installed by `02-deploy-azureml-extension.sh` (`gpuspot`, `gpu`, `gpuspot2`, …) select on `accelerator: nvidia`. That label is normally applied at runtime by NFD / GPU Operator on already-running GPU nodes. When the pool is at zero, the cluster autoscaler builds a synthetic node template from **static** AKS-side labels only (transmitted to it via VMSS tags) and never sees `accelerator=nvidia` — so it concludes that scaling the pool up would not satisfy the pending Pod, and refuses.

The fix is to declare the label statically on every GPU pool via Terraform:

```hcl
node_labels = {
  accelerator = "nvidia"
}
```

Already wired into the default `gpu` pool in `infrastructure/terraform/variables.tf` and `infrastructure/terraform/modules/sil/variables.tf`. Any custom GPU pool added via `node_pools` in `terraform.tfvars` must include the same label. NFD and the static label coexist without conflict.

### Volcano enqueue-time capacity gate

The Azure ML extension installs Volcano with `overcommit` and `proportion` plugins in the third tier of its scheduler config. Both implement Volcano's `JobEnqueueable` interface and gate the `enqueue` action against currently-Ready cluster capacity (`proportion`: `requested ≤ queue.Allocated + queue.Free`; `overcommit`: `requested ≤ total × overcommit-factor`).

On a cluster whose GPU pools sit at `count = 0`, the GPU capacity term is `0 × 1.2 = 0`, so every GPU PodGroup fails enqueue and stays in phase `Pending` forever. Because Volcano only creates the underlying Pod once the PodGroup reaches `Inqueue`, no Pending Pod ever appears in kube-scheduler's queue — and without a Pending Pod, the AKS cluster autoscaler has nothing to scale up against.

`02-deploy-azureml-extension.sh` patches `volcano-scheduler-configmap` with [`infrastructure/setup/manifests/volcano-scheduler-config-scale-from-zero.conf`](../../infrastructure/setup/manifests/volcano-scheduler-config-scale-from-zero.conf) (both plugins removed from tier 3) and restarts `volcano-scheduler` after extension install. Gang scheduling is preserved because the `gang` plugin still gates the `allocate` action — multi-pod jobs continue to wait for `minAvailable` before any task starts.

Override with `--enforce-volcano-capacity-check` on multi-tenant clusters where queue-level capacity fairness must be enforced at submit time. Scale-from-zero will then be impossible without keeping at least one GPU node warm (`min_count ≥ 1`).

### Verifying scale-up

```bash
# Submit a job, then watch the autoscaler decision.
kubectl -n kube-system get cm cluster-autoscaler-status -o jsonpath='{.data.status}' | head
# Expected progression:
#   scaleUp.status: NoActivity -> InProgress
#   nodeGroups[aks-<pool>-vmss].cloudProviderTarget: 0 -> 1
```

If `scaleUp.status` stays `NoActivity` after submission, walk the three layers in order:

1. `kubectl -n azureml logs deploy/aml-operator` — look for `"resource validation failed"` (operator layer).
2. `kubectl get podgroup -n azureml` — phase `Pending` with `Unschedulable: resource in cluster is overused` is the Volcano enqueue gate.
3. `kubectl describe pod -n azureml <worker>` — `FailedScheduling: 0/N nodes are available, ... node(s) didn't match Pod's node affinity/selector` is the missing-label layer.

## 📚 Related Documentation

- [LeRobot Training](lerobot-training.md)
- [OSMO Training](osmo-training.md)
- [MLflow Integration](mlflow-integration.md)
- [Training Guide](README.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
