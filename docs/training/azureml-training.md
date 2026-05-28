---
sidebar_position: 2
title: Azure ML Training Workflows
description: Submit Isaac Lab and LeRobot training jobs to Azure Machine Learning
author: Microsoft Robotics-AI Team
ms.date: 2026-05-26
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

| Template             | Purpose                    | Submission Script                            |
|----------------------|----------------------------|----------------------------------------------|
| `train.yaml`         | Isaac Lab SKRL training    | `scripts/submit-azureml-training.sh`         |
| `validate.yaml`      | Isaac Lab validation       | `scripts/submit-azureml-validation.sh`       |
| `lerobot-train.yaml` | LeRobot behavioral cloning | `scripts/submit-azureml-lerobot-training.sh` |

## ⚙️ Isaac Lab Training Parameters

| Parameter         | Description                                         |
|-------------------|-----------------------------------------------------|
| `mode`            | Train or retrain (default: `train`)                 |
| `checkpoint_mode` | Checkpoint strategy: `from-scratch`, `from-trained` |
| `task`            | Isaac Lab task name (e.g., `Isaac-Cartpole-v0`)     |
| `num_envs`        | Number of parallel environments                     |
| `headless`        | Run without rendering (default: `true`)             |
| `max_iterations`  | Maximum training iterations                         |

## 🤖 LeRobot Training Parameters

| Parameter         | Default                                          | Description                               |
|-------------------|--------------------------------------------------|-------------------------------------------|
| `dataset_repo_id` | (required)                                       | HuggingFace dataset repository            |
| `policy_type`     | `act`                                            | Policy architecture: `act`, `diffusion`   |
| `job_name`        | `lerobot-act-training`                           | Unique job identifier                     |
| `image`           | `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime` | Container image                           |
| `save_freq`       | `5000`                                           | Checkpoint save frequency                 |
| `instance_type`   | `gpuspot`                                        | Pod size (AzureML-on-Kubernetes only)     |
| `mixed_precision` | `no`                                             | Accelerate mixed precision (no/fp16/bf16) |

### Single-node multi-GPU training

LeRobot training on Azure ML supports single-node multi-GPU execution via [Hugging Face Accelerate](https://huggingface.co/docs/lerobot/multi_gpu_training). The wrapper detects the visible GPU count at runtime via `torch.cuda.device_count()` and, when `N > 1`, automatically launches `accelerate launch --multi_gpu --num_processes=N`. No AzureML `distribution:` block is required because the run stays within one process group on one node.

Both AzureML compute backends are supported. GPU count is determined by the backend:

- **AzureML managed compute (`AmlCompute`):** GPU count visible to the job container equals the cluster's VM SKU GPU count (e.g., `Standard_NC48ads_A100_v4` → 2, `Standard_NC96ads_A100_v4` → 4). Pass `--compute <cluster-name>` (matching an entry in `aml_compute_clusters`).
- **AzureML-on-Kubernetes (Arc-attached AKS):** GPU count visible to the job container is the `InstanceType` CRD's `nvidia.com/gpu: N` request. `gpu2`/`gpuspot2`/`gpu4`/`gpuspot4` are shipped in `infrastructure/setup/manifests/azureml-instance-types.yaml` and require a node SKU with at least `N` GPUs (e.g., `Standard_NC128ds_xl_RTXPRO6000BSE_v6` for `N=4`).

Managed compute example:

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --dataset-repo-id user/dataset \
  --compute gpu-training \
  --mixed-precision bf16 \
  --batch-size 8
```

AzureML-on-Kubernetes example:

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --dataset-repo-id user/dataset \
  --instance-type gpu4 \
  --mixed-precision bf16 \
  --batch-size 8
```

> [!NOTE]
> LeRobot does NOT auto-scale the learning rate or training steps with GPU count. The effective batch size is `batch_size × num_gpus` (logged to MLflow as `effective_batch_size`); adjust `--steps` and `--learning-rate` manually if you want to match a single-GPU baseline. The `--policy.use_amp` flag is ignored under Accelerate and is stripped by the wrapper with a warning.

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
./scripts/submit-azureml-training.sh

# Custom task and environment count
./scripts/submit-azureml-training.sh \
  --task Isaac-Cartpole-v0 \
  --num-envs 512 \
  --max-iterations 1000
```

Isaac Lab validation:

```bash
./scripts/submit-azureml-validation.sh \
  --task Isaac-Cartpole-v0 \
  --checkpoint-mode from-trained
```

LeRobot training:

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --dataset-repo-id lerobot/aloha_sim_insertion_human \
  --policy-type act
```

## 💾 Checkpoint Management

| Mode           | Behavior                                  |
|----------------|-------------------------------------------|
| `from-scratch` | Start training from random initialization |
| `from-trained` | Resume from an existing checkpoint        |

Specify the checkpoint mode with `--checkpoint-mode`:

```bash
./scripts/submit-azureml-training.sh \
  --checkpoint-mode from-trained \
  --task Isaac-Cartpole-v0
```

## 📚 Related Documentation

- [LeRobot Training](lerobot-training.md)
- [OSMO Training](osmo-training.md)
- [MLflow Integration](mlflow-integration.md)
- [Training Guide](README.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
