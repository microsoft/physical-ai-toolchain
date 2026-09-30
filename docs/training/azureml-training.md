---
sidebar_position: 2
title: Azure ML Training Workflows
description: Submit Isaac Lab and LeRobot training jobs to Azure Machine Learning
author: Microsoft Robotics-AI Team
ms.date: 2026-09-30
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
| `gpu-smoke.yaml`           | GPU target smoke test      | `training/smoke/scripts/submit-azureml-gpu-smoke.sh`           |

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
- **AzureML-on-Kubernetes (Arc-attached AKS):** GPU count visible to the job container is the `InstanceType` CRD's `nvidia.com/gpu: N` request. `gpu2`/`gpuspot2`/`gpu4`/`gpuspot4` are shipped in `infrastructure/setup/manifests/azureml-instance-types.yaml` and require a node SKU with at least `N` GPUs (e.g., `Standard_NC96ads_A100_v4` for `N=4`, or `Standard_NC288ds_xl_RTXPRO6000BSE_v6` for `N=2`).

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

## 🩺 Smoke-Test a GPU Target

Before you commit GPU time to training, prove that an InstanceType gets a working GPU and that the services training depends on are reachable from inside a job:

```bash
./training/smoke/scripts/submit-azureml-gpu-smoke.sh --instance-type gpu-a10-1x --stream
```

The job trains a small model for 200 steps on one GPU, which takes a few minutes. It runs these checks, and a failure in one doesn't stop the others:

| Check              | Passes when                                                                                           |
|--------------------|-------------------------------------------------------------------------------------------------------|
| `device`           | PyTorch sees a CUDA device (it records the GPU, driver, and CUDA versions)                            |
| `matmul`           | A timed half-precision matmul matches a float64 reference                                             |
| `azure_workspace`  | The job identity gets a token, reads the workspace, and configures MLflow tracking                    |
| `mlflow_run`       | The job attaches to its Azure ML MLflow run and logs parameters                                       |
| `training`         | A small network trains on the GPU and its loss falls below half the starting loss                     |
| `mlflow_metrics`   | Every per-step loss metric reads back from MLflow                                                     |
| `checkpoints`      | Periodic checkpoints exist in the `checkpoints` output, and the final one reloads intact              |
| `mlflow_artifacts` | The final checkpoint uploads as an MLflow artifact and downloads back intact through its `runs:/` URI |
| `storage`          | The identity uploads a checkpoint to the Terraform storage account; the test blob is then deleted     |
| `model_registry`   | The final checkpoint registers as a `custom_model` and reads back                                     |

The `mlflow_artifacts` check downloads rather than lists, because MLflow 3 lists `runs:/` paths through a logged-model search that Azure ML's MLflow endpoint doesn't implement (HTTP 404). Downloads through `runs:/` URIs, which training uses to resume from checkpoints, work.

With `--stream`, the script waits for the job, downloads its `checkpoints` output, and prints each check's result from `smoke-summary.json`. It exits non-zero unless the job completed and every check passed. Checks that depend on a failed check are reported as skipped, so start with the first failure.

Each run leaves a job and its MLflow run in the `gpu-smoke` experiment and a new `gpu-smoke-test` model version. Pass `--skip-register-model` to skip the registry check, or `--storage-account ""` to skip the storage check.

The image is PyTorch with CUDA 12.4, which runs on NVIDIA driver 550 and newer. That includes the GRID drivers AKS installs on A10 nodes. Training images built for CUDA 13 need driver 580 or newer, so a passing smoke test doesn't prove those images run on an older driver.

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

The SiL module's default GPU pool uses `min_count = 0`; the root deployment's default GPU pool uses `min_count = 1`. Check the effective `node_pools` configuration before assuming scale-to-zero. For a pool configured to reach zero, the setup script overrides two scheduler checks, and the pool's InstanceTypes must select it by `agentpool`, as described below.

### `aml-operator` resource validation

The Azure ML Kubernetes extension installs `aml-operator`, which runs a pre-flight check on every submitted `AmlJob`:

> Does the requested `InstanceType` fit inside the largest currently-Ready node?

With the chart default `amloperator.skipResourceValidation: false`, the operator fails the job immediately with `Code: 9` ("Invalid instance type. The instance type defined resource requirement has exceeded the node size") whenever the target GPU pool is at zero. No Pod is created, kube-scheduler is never invoked, and the cluster autoscaler never observes a pending Pod to scale up against.

Result: a permanent deadlock — you cannot submit the job that would cause the GPU resource to become available.

`02-deploy-azureml-extension.sh` sets `amloperator.skipResourceValidation=true` by default, both when it installs the extension and on reruns against an existing extension whose live setting differs. Extensions installed before the script managed this setting keep the chart default until you rerun it. Override with `--enforce-resource-validation` on fixed-capacity clusters where you want misconfigured InstanceTypes to fail fast at submission rather than producing Pods stuck in `Pending`.

Trade-off when enabled (the default): a typo in an `InstanceType` (e.g. `nvidia.com/gpu: 8` on a 4-GPU SKU) manifests as `FailedScheduling` events on a long-Pending Pod instead of an immediate job failure. Diagnose with `kubectl describe pod`.

### Select GPU pools by `agentpool`

The default InstanceTypes in [`infrastructure/setup/manifests/azureml-instance-types.yaml`](../../infrastructure/setup/manifests/azureml-instance-types.yaml) (`gpuspot`, `gpu`, `gpuspot2`, …) select on `accelerator: nvidia`. AKS sets that label on GPU nodes when they join the cluster, so these InstanceTypes match running GPU nodes. For current GPU sizes, they can't wake a pool at zero.

When a pool is at zero, the cluster autoscaler builds a node template from the pool's VMSS. The template carries the pool's `agentpool` label, but it predicts `accelerator=nvidia` only for older GPU sizes (K80 through A100), not for A10, H100, or RTX PRO 6000 sizes. The autoscaler then concludes that a new node wouldn't satisfy the pending Pod, and doesn't scale up.

You can't declare the label yourself: AKS reserves `accelerator` and rejects it in `node_labels` with `NodeLabelKeyNotAllowed`, and the Terraform module rejects it at plan time.

For pools that scale from zero, apply InstanceTypes that select the pool by name:

```yaml
nodeSelector:
  agentpool: <pool key>
  kubernetes.azure.com/scalesetpriority: spot # Spot pools only
```

The environment deployment bundle generates InstanceTypes this way from Terraform outputs. Apply them with `02-deploy-azureml-extension.sh --instance-types-manifest`.

### Volcano enqueue-time capacity gate

The Azure ML extension installs Volcano with `overcommit` and `proportion` plugins in the third tier of its scheduler config. Both implement Volcano's `JobEnqueueable` interface and gate the `enqueue` action against currently-Ready cluster capacity (`proportion`: `requested ≤ queue.Allocated + queue.Free`; `overcommit`: `requested ≤ total × overcommit-factor`).

On a cluster whose GPU pools sit at `count = 0`, the GPU capacity term is `0 × 1.2 = 0`, so every GPU PodGroup fails enqueue and stays in phase `Pending` forever. Because Volcano only creates the underlying Pod once the PodGroup reaches `Inqueue`, no Pending Pod ever appears in kube-scheduler's queue — and without a Pending Pod, the AKS cluster autoscaler has nothing to scale up against.

`02-deploy-azureml-extension.sh` creates the `volcano-scheduler-scale-from-zero` configmap in the `azureml` namespace from [`infrastructure/setup/manifests/volcano-scheduler-config-scale-from-zero.conf`](../../infrastructure/setup/manifests/volcano-scheduler-config-scale-from-zero.conf) (both plugins removed from tier 3). It then points the extension at that configmap through the `volcanoScheduler.schedulerConfigMap` extension setting.

Gang scheduling is preserved because the `gang` plugin still gates the `allocate` action — multi-pod jobs continue to wait for `minAvailable` before any task starts.

The extension upgrades itself automatically, and each upgrade restores the chart's own `volcano-scheduler-configmap`, so an in-place edit of that configmap doesn't last. Extension settings do persist across upgrades. Reruns update only the settings that differ from the live extension, and restart `volcano-scheduler` when only the configmap content changed. `cleanup/uninstall-azureml-extension.sh` deletes the `azureml` namespace, which removes the configmap.

Override with `--enforce-volcano-capacity-check` on multi-tenant clusters where queue-level capacity fairness must be enforced at submit time. It resets `volcanoScheduler.schedulerConfigMap` to the chart's config. Scale-from-zero will then be impossible without keeping at least one GPU node warm (`min_count ≥ 1`).

### Verifying scale-up

Confirm the extension settings and the scheduler config first:

```bash
az k8s-extension show --name azureml-<aks-cluster> --cluster-type managedClusters \
  --cluster-name <aks-cluster> --resource-group <resource-group> \
  --query 'configurationSettings.{skipResourceValidation: "amloperator.skipResourceValidation", volcanoConfigMap: "volcanoScheduler.schedulerConfigMap"}'
# Expected: skipResourceValidation "true", volcanoConfigMap "volcano-scheduler-scale-from-zero"

kubectl get cm -n azureml volcano-scheduler-scale-from-zero \
  -o jsonpath='{.data.volcano-scheduler\.conf}' | grep -E 'overcommit|proportion'
# Expected: no output
```

Then submit a job and follow the autoscaler:

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
