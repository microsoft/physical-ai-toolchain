---
sidebar_position: 5
title: LeRobot Training
description: Behavioral cloning training with ACT and Diffusion policies on Azure ML and OSMO platforms
author: Microsoft Robotics-AI Team
ms.date: 2026-05-26
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

LeRobot behavioral cloning training for ACT and Diffusion policy architectures. Training runs on Azure ML and OSMO platforms using HuggingFace Hub, Azure Blob, and AzureML data asset sources (Azure ML only), with MLflow experiment tracking.

## 📋 Prerequisites

| Component         | Requirement                                                                                                                    |
|-------------------|--------------------------------------------------------------------------------------------------------------------------------|
| Infrastructure    | AKS cluster deployed via [Infrastructure Guide](https://github.com/microsoft/physical-ai-toolchain/blob/main/deploy/README.md) |
| Azure ML or OSMO  | At least one platform configured (see Platform Selection section)                                                              |
| HuggingFace token | Required only for private HuggingFace datasets (`hf_token` credential); Azure Blob and Data Asset sources use managed identity |

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

| Aspect              | Azure ML                                            | OSMO                                      |
|---------------------|-----------------------------------------------------|-------------------------------------------|
| Submission          | `az ml job create`                                  | `osmo workflow submit`                    |
| Experiment tracking | MLflow (managed)                                    | MLflow (Azure ML backend)                 |
| Credential handling | Azure ML environment variables                      | `osmo credential set` injection           |
| Dataset delivery    | HuggingFace Hub, Azure Blob, or AzureML data assets | HuggingFace Hub or direct Azure Blob URLs |
| Pipeline support    | Manual multi-step                                   | `run-lerobot-pipeline.sh` orchestration   |

## ⚙️ Training Configuration

| Parameter                  | Default                                              | Description                                                                       |
|----------------------------|------------------------------------------------------|-----------------------------------------------------------------------------------|
| `--dataset-repo-id`        | Required for HuggingFace; `dataset` for Blob sources | HuggingFace dataset repository or logical local dataset name                      |
| `--blob-url`               | (none)                                               | Direct Azure Blob dataset URL; repeat for multiple sources                        |
| `--policy-type`            | `act`                                                | Policy: `act` or `diffusion`                                                      |
| `--job-name`               | `lerobot-act-training`                               | Job identifier                                                                    |
| `--image`                  | `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime`     | Container image                                                                   |
| `--training-steps`         | `100000`                                             | Total training iterations                                                         |
| `--batch-size`             | `32`                                                 | Training batch size                                                               |
| `--save-freq`              | `5000`                                               | Checkpoint save frequency                                                         |
| `--policy-repo-id`         | (none)                                               | Pre-trained policy for fine-tuning                                                |
| `--init-from-policy-model` | (none)                                               | Warm-start from a registered AzureML model (`azureml:NAME:VERSION`); AzureML only |

### Fine-Tuning from Existing Policy

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  --policy-repo-id user/pretrained-act \
  --training-steps 50000 \
  --batch-size 16
```

### Warm-Starting from a Registered AzureML Model (AzureML only)

After a previous AzureML run has registered a checkpoint with `--register-checkpoint NAME`, a follow-up AzureML run can seed weights from that model. Optimizer state, scheduler state, and the step counter are not restored — only the policy weights — so this is "warm-start" rather than "resume". Not yet supported by the OSMO submission script.

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  -d user/my-dataset \
  --init-from-policy-model azureml:my-act-policy:7 \
  --training-steps 50000
```

Accepted URI forms:

- `azureml:NAME:VERSION` — version must be numeric. `azureml:NAME@latest` and bare `azureml:NAME` are rejected to keep submissions reproducible.
- `azureml://locations/.../models/NAME/versions/VERSION` — fully-qualified workspace asset URI.
- `https://...blob.core.windows.net/...` — direct blob URL pointing at a folder containing `config.json` and `model.safetensors`.

The MLflow run for the new job is tagged with `warm_start.source`, and (for `azureml:NAME:VERSION` inputs) `warm_start.model_name` and `warm_start.model_version` so runs can be filtered by upstream model in the MLflow UI.

## 🔑 Credential Setup

### OSMO Credentials

OSMO injects credentials at workflow runtime:

```bash
# HuggingFace token (required for private datasets)
osmo credential set hf_token --generic --value "hf_..."
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

### MLflow (Azure ML Managed)

Azure ML training uses workspace MLflow automatically. OSMO LeRobot workflows log to the same Azure ML workspace resolved from Terraform outputs or the `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZUREML_WORKSPACE_NAME` environment variables.

Both submission scripts pin `policy.push_to_hub=false`. Checkpoints are registered to the Azure ML model registry when `--register-checkpoint NAME` is passed; HuggingFace Hub upload is never used.

See [Experiment Tracking](experiment-tracking.md) for platform comparison and configuration details.

## 💾 Dataset Workflows

Supported dataset delivery differs by platform.

| Platform | Supported sources                                                                                     |
|----------|-------------------------------------------------------------------------------------------------------|
| OSMO     | HuggingFace Hub or Azure Blob                                                                         |
| Azure ML | HuggingFace Hub, direct Azure Blob URLs, AzureML data assets, or combined Blob and data asset sources |

### HuggingFace Hub (Default)

LeRobot downloads datasets from HuggingFace Hub at runtime. Specify datasets with `--dataset-repo-id`:

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### Azure Blob Storage (OSMO)

Train from direct Azure Blob URLs using OSMO workload identity. Blob submissions do not require a HuggingFace dataset repository; the script defaults the local dataset ID to `dataset`.

```bash
./scripts/submit-osmo-lerobot-training.sh \
  --blob-url "https://mystorageaccount.blob.core.windows.net/training/pusht" \
  -r pusht-model
```

Use multiple `--blob-url` values to merge compatible datasets before training:

```bash
./scripts/submit-osmo-lerobot-training.sh \
  --blob-url "https://account1.blob.core.windows.net/train/set1" \
  --blob-url "https://account2.blob.core.windows.net/train/set2" \
  -r merged-pusht-model
```

> [!IMPORTANT]
> OSMO accepts plain HTTPS Azure Blob URLs only. OSMO authenticates with its workload identity; grant that identity `Storage Blob Data Reader`, `Storage Blob Data Contributor`, or `Storage Blob Data Owner` on each storage account or container. AzureML data asset identifiers, datastore URIs, ADLS Gen2 URLs, Azure Files, OneLake, local paths, fragments, and any query string (including SAS tokens) are rejected.

### Azure Blob Storage (AzureML)

Train directly from Azure Blob Storage datasets using managed identity authentication. Supports single or multiple datasets with automatic merging.

#### Single Dataset

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --blob-url "https://mystorageaccount.blob.core.windows.net/training/pusht" \
  -r pusht-model
```

#### Multiple Datasets (Automatic Merge)

Combine datasets from different containers or storage accounts:

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --blob-url "https://account1.blob.core.windows.net/train/set1" \
  --blob-url "https://account1.blob.core.windows.net/train/set2" \
  -r merged-pusht-model
```

LeRobot automatically validates dataset compatibility and merges them before training.

### AzureML Data Asset (Native Mount, AzureML only)

Use registered AzureML data assets, mounted read-only into the training container via AzureML's native `ro_mount` mechanism. No download step is required — datasets are available immediately at a FUSE mount path.

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --dataset-asset azureml:pusht-episodes:3 \
  -r pusht-model
```

Multiple data assets can be merged:

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --dataset-asset azureml:episodes-day1:2 \
  --dataset-asset azureml:episodes-day2:1 \
  -r merged-model
```

The data asset URI must be version-pinned (`azureml:NAME:VERSION` or the full ARM path `azureml://.../data/NAME/versions/VERSION`). Shorthands like `@latest` are rejected to keep runs reproducible.

### Combined Sources (AzureML only)

Data assets and blob URLs can be combined. All sources are merged automatically via `lerobot-edit-dataset`:

```bash
./scripts/submit-azureml-lerobot-training.sh \
  --dataset-asset azureml:pusht-base:3 \
  --blob-url "https://account.blob.core.windows.net/extra/pusht" \
  -r combined-model
```

## 🔒 Runtime Dependency Lockfile

AzureML LeRobot jobs install `training/il/lerobot/requirements.txt` with `uv pip install --no-deps`, so the compiled lockfile is the runtime contract. Regenerate it after any `training/il/lerobot/pyproject.toml` change:

```bash
cd training/il/lerobot
uv pip compile pyproject.toml -o requirements.txt --python-version 3.12 --python-platform manylinux_2_28_x86_64
```

The Linux platform flag is intentional. It matches the AzureML CUDA container rather than the developer workstation and prevents macOS-only wheels from entering the lockfile. It can select older but compatible transitive versions than a local unconstrained compile; for example, `lerobot==0.5.1` requires `torch<2.11`, so the Linux lockfile uses the latest resolver-compatible Torch 2.10 series instead of the invalid Torch 2.12 output produced by an unconstrained local compile.

Some downgraded packages are corrections, not regressions: `av<16` and `cmake<4.2` come from LeRobot's declared constraints. Security-sensitive pins such as `gitpython` and `urllib3` remain explicit in `pyproject.toml`; any older transitive version introduced by the Linux resolver should be reviewed before committing the regenerated lockfile.

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

- [Experiment Tracking](experiment-tracking.md) for MLflow configuration
- [AzureML Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/azureml/README.md) for job template reference
- [OSMO Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/osmo/README.md) for workflow template reference
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
