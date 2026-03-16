---
sidebar_position: 5
title: LeRobot Training
description: Behavioral cloning training with ACT and Diffusion policies on Azure ML and OSMO platforms
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: how-to
keywords:
  - lerobot
  - behavioral cloning
  - act
  - diffusion
  - azureml
  - osmo
  - training
---

LeRobot behavioral cloning training for ACT and Diffusion policy architectures. Training runs on Azure ML and OSMO platforms using HuggingFace Hub datasets with WANDB and MLflow experiment tracking.

## 📋 Prerequisites

| Component         | Requirement                                                             |
|-------------------|-------------------------------------------------------------------------|
| Infrastructure    | AKS cluster deployed via [Infrastructure Guide](https://github.com/microsoft/physical-ai-toolchain/blob/main/deploy/README.md) |
| Azure ML or OSMO  | At least one platform configured (see Platform Selection section)       |
| HuggingFace token | Required for private datasets (`hf_token` credential)                   |
| WANDB API key     | Required when `--wandb-enable` is set (default on OSMO)                 |

## 🚀 Quick Start

### Azure ML

```bash
./scripts/submit-azureml-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### OSMO

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### End-to-End Pipeline (OSMO)

Train, evaluate, and register in one command:

```bash
./scripts/run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id user/my-act-policy \
  -r my-act-model
```

## 🧠 Policy Architectures

| Architecture | Type                              | Strengths                                 |
|--------------|-----------------------------------|-------------------------------------------|
| ACT          | Action Chunking with Transformers | Multi-step prediction, temporal coherence |
| Diffusion    | Denoising Diffusion Policy        | Multi-modal action distributions          |

Select the architecture with `--policy-type`:

```bash
# ACT policy (default)
./scripts/submit-osmo-lerobot-training.sh -d user/dataset -p act

# Diffusion policy
./scripts/submit-osmo-lerobot-training.sh -d user/dataset -p diffusion
```

## ⚖️ Platform Selection

| Aspect              | Azure ML                       | OSMO                                    |
|---------------------|--------------------------------|-----------------------------------------|
| Submission          | `az ml job create`             | `osmo workflow submit`                  |
| Experiment tracking | MLflow (managed)               | WANDB (default) + MLflow (optional)     |
| Credential handling | Azure ML environment variables | `osmo credential set` injection         |
| Dataset delivery    | HuggingFace Hub download       | Hub download or OSMO bucket mount       |
| Pipeline support    | Manual multi-step              | `run-lerobot-pipeline.sh` orchestration |

## ⚙️ Training Configuration

| Parameter           | Default                                         | Description                        |
|---------------------|-------------------------------------------------|------------------------------------|
| `--dataset-repo-id` | (required)                                      | HuggingFace dataset repository     |
| `--policy-type`     | `act`                                           | Policy: `act` or `diffusion`       |
| `--job-name`        | `lerobot-act-training`                          | Job identifier                     |
| `--image`           | `pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime` | Container image                    |
| `--training-steps`  | (LeRobot default)                               | Total training iterations          |
| `--batch-size`      | (LeRobot default)                               | Training batch size                |
| `--save-freq`       | `5000`                                          | Checkpoint save frequency          |
| `--policy-repo-id`  | (none)                                          | Pre-trained policy for fine-tuning |

### Fine-Tuning from Existing Policy

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  --policy-repo-id user/pretrained-act \
  --training-steps 50000 \
  --batch-size 16
```

## 🔑 Credential Setup

### OSMO Credentials

OSMO injects credentials at workflow runtime:

```bash
# HuggingFace token (required for private datasets)
osmo credential set hf_token --generic --value "hf_..."

# WANDB API key (required when wandb_enable=true)
osmo credential set wandb_api_key --generic --value "..."
```

### Azure ML Credentials

Azure ML uses workspace-managed identity. Set environment variables for custom configurations:

| Variable                 | Description             |
|--------------------------|-------------------------|
| `AZURE_SUBSCRIPTION_ID`  | Azure subscription ID   |
| `AZURE_RESOURCE_GROUP`   | Resource group name     |
| `AZUREML_WORKSPACE_NAME` | Azure ML workspace name |
| `AZUREML_COMPUTE`        | Compute target name     |

## 📊 Experiment Logging

### WANDB (Default on OSMO)

WANDB logging is enabled by default on OSMO workflows. Requires `wandb_api_key` credential.

```bash
# Disable WANDB
./scripts/submit-osmo-lerobot-training.sh \
  -d user/dataset \
  --wandb-disable
```

### MLflow (Azure ML Managed)

Azure ML training uses MLflow automatically. Enable MLflow on OSMO with:

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d user/dataset \
  --mlflow-enable
```

See [Experiment Tracking](experiment-tracking.md) for platform comparison and configuration details.

## 💾 Dataset Workflows

### HuggingFace Hub (Default)

LeRobot downloads datasets from HuggingFace Hub at runtime. Specify datasets with `--dataset-repo-id`:

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### OSMO Dataset Mount

Mount datasets from OSMO buckets backed by Azure Blob Storage:

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -w workflows/osmo/lerobot-train-dataset.yaml \
  -d user/fallback-dataset \
  --dataset-bucket my-bucket \
  --dataset-name my-lerobot-data
```

Falls back to HuggingFace Hub download when no dataset mount is available.

## 🔄 End-to-End Pipeline

The `run-lerobot-pipeline.sh` script orchestrates the full lifecycle on OSMO:

| Stage | Action                                |
|-------|---------------------------------------|
| 1     | Submit training workflow              |
| 2     | Poll workflow status until completion |
| 3     | Submit inference/evaluation workflow  |

```bash
# Full pipeline
./scripts/run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id user/my-policy \
  -r my-model

# Training only with polling (skip inference)
./scripts/run-lerobot-pipeline.sh \
  -d user/dataset \
  --skip-inference

# Async mode (submit and exit)
./scripts/run-lerobot-pipeline.sh \
  -d user/dataset \
  --skip-wait
```

## 🔗 Related Documentation

- [LeRobot Inference](../inference/lerobot-inference.md) for evaluating trained policies
- [Experiment Tracking](experiment-tracking.md) for MLflow and WANDB configuration
- [AzureML Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/azureml/README.md) for job template reference
- [OSMO Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/osmo/README.md) for workflow template reference
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
