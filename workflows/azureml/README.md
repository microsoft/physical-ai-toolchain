---
title: AzureML Workflows
description: Azure Machine Learning job templates for robotics training and validation
author: Edge AI Team
ms.date: 2026-03-14
ms.topic: reference
---

Azure Machine Learning job templates for Isaac Lab training and validation workloads.

## 📜 Available Templates

| Template                                 | Purpose                               | Submission Script                            |
|------------------------------------------|---------------------------------------|----------------------------------------------|
| [train.yaml](train.yaml)                 | Training jobs with checkpoint support | `scripts/submit-azureml-training.sh`         |
| [validate.yaml](validate.yaml)           | Policy validation and inference       | `scripts/submit-azureml-validation.sh`       |
| [lerobot-train.yaml](lerobot-train.yaml) | LeRobot behavioral cloning training   | `scripts/submit-azureml-lerobot-training.sh` |

## 🏋️ Training Job (`train.yaml`)

Submits Isaac Lab reinforcement learning training to AKS GPU nodes via Azure ML.

### Key Parameters

| Input             | Description                     | Default                            |
|-------------------|---------------------------------|------------------------------------|
| `mode`            | Execution mode                  | `train`                            |
| `checkpoint_mode` | Checkpoint loading strategy     | `from-scratch`                     |
| `task`            | Isaac Lab task name             | `Isaac-Velocity-Rough-Anymal-C-v0` |
| `num_envs`        | Number of parallel environments | `4096`                             |
| `headless`        | Run without rendering           | `true`                             |
| `max_iterations`  | Training iterations             | `4500`                             |

### Training Usage

```bash
# Default configuration from Terraform outputs
./scripts/submit-azureml-training.sh

# Override specific parameters
./scripts/submit-azureml-training.sh \
  --resource-group rg-custom \
  --workspace-name mlw-custom
```

## ✅ Validation Job (`validate.yaml`)

Runs trained policy validation and generates inference metrics.

### Validation Parameters

| Input             | Description                 | Default                            |
|-------------------|-----------------------------|------------------------------------|
| `mode`            | Execution mode              | `play`                             |
| `checkpoint_mode` | Must use trained checkpoint | `from-trained`                     |
| `task`            | Isaac Lab task name         | `Isaac-Velocity-Rough-Anymal-C-v0` |
| `num_envs`        | Environments for validation | `1024`                             |

### Validation Usage

```bash
# Default configuration
./scripts/submit-azureml-validation.sh

# With custom checkpoint
./scripts/submit-azureml-validation.sh \
  --checkpoint-path "azureml://datastores/checkpoints/paths/model.pt"
```

## ⚙️ Environment Variables

All scripts support environment variable configuration:

| Variable                 | Description             |
|--------------------------|-------------------------|
| `AZURE_SUBSCRIPTION_ID`  | Azure subscription ID   |
| `AZURE_RESOURCE_GROUP`   | Resource group name     |
| `AZUREML_WORKSPACE_NAME` | Azure ML workspace name |
| `AZUREML_COMPUTE`        | Compute target name     |

## 📋 Prerequisites

1. Azure ML extension installed on AKS cluster
2. Kubernetes compute target attached to workspace
3. GPU instance types configured in cluster

## 🤖 LeRobot Training Job (`lerobot-train.yaml`)

Submits LeRobot behavioral cloning training (ACT/Diffusion policies) to Azure ML. Installs LeRobot dynamically in the container and trains from HuggingFace Hub datasets.

### LeRobot Parameters

| Input             | Description                    | Default                                         |
|-------------------|--------------------------------|-------------------------------------------------|
| `dataset_repo_id` | HuggingFace dataset repository | (required)                                      |
| `policy_type`     | Policy architecture            | `act`                                           |
| `job_name`        | Job identifier                 | `lerobot-act-training`                          |
| `image`           | Container image                | `pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime` |
| `wandb_enable`    | Enable WANDB logging           | `true`                                          |
| `save_freq`       | Checkpoint save frequency      | `5000`                                          |

### LeRobot Usage

```bash
# ACT policy training
./scripts/submit-azureml-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human

# Diffusion policy with model registration
./scripts/submit-azureml-lerobot-training.sh \
  -d user/custom-dataset \
  -p diffusion \
  -r my-diffusion-model \
  --stream
```

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
