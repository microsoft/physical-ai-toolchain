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
| GR00T-N1.5   | Vision-Language-Action Foundation | Multi-embodiment, language-conditioned    |
| GR00T-N1.7   | Vision-Language-Action Foundation | N1.5 + improved modality config pipeline  |

Select the architecture with `--policy-type`:

```bash
# ACT policy (default)
./scripts/submit-osmo-lerobot-training.sh -d user/dataset -p act

# Diffusion policy
./scripts/submit-osmo-lerobot-training.sh -d user/dataset -p diffusion

# GR00T-N1.5 fine-tuning (VLA — separate script)
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --vla-version 1.5 \
  --base-model nvidia/GR00T-N1.5-3B \
  --data-config example \
  --data-config-file training/vla/configs/groot/examples/data_config.py \
  --blob-url https://myaccount.blob.core.windows.net/datasets/my-data

# GR00T-N1.7 fine-tuning (VLA — separate script)
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --vla-version 1.7 \
  --base-model nvidia/GR00T-N1.7-3B \
  --data-config example \
  --modality-config-file training/vla/configs/groot/examples/modality_config.py \
  --blob-url https://myaccount.blob.core.windows.net/datasets/my-data
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

| Parameter                  | Default                                              | Description                                                                           |
|----------------------------|------------------------------------------------------|---------------------------------------------------------------------------------------|
| `--dataset-repo-id`        | Required for HuggingFace; `dataset` for Blob sources | HuggingFace dataset repository or logical local dataset name                      |
| `--blob-url`               | (none)                                               | Direct Azure Blob dataset URL; repeat for multiple sources                            |
| `--policy-type`            | `act`                                                | Policy: `act`, `diffusion`                                               , or `groot` |
| `--job-name`               | `lerobot-act-training`                               | Job identifier                                                                        |
| `--image`                  | `pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime`     | Container image                                                                       |
| `--training-steps`         | `100000`                                             | Total training iterations                                                             |
| `--batch-size`             | `32`                                                 | Training batch size                                                                   |
| `--save-freq`              | `5000`                                               | Checkpoint save frequency                                                             |
| `--policy-repo-id`         | (none)                                               | Pre-trained policy for fine-tuning                                                    |
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
osmo credential set huggingface --type GENERIC --payload hf_token="hf_..."
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

AzureML LeRobot jobs derive their runtime dependencies at build time from the committed `training/il/lerobot/uv.lock`, the single resolution source of truth. The entrypoints run `uv export --frozen --no-hashes --no-emit-project` and pipe the result into `uv pip install --no-deps`, so the lock — not a committed flat file — is the runtime contract. Regenerate the lock after any `training/il/lerobot/pyproject.toml` change:

```bash
cd training/il/lerobot
uv lock
```

`[tool.uv] environments` constrains the universal lock to the AzureML CUDA target (`sys_platform == 'linux' and platform_machine == 'x86_64'`), so `uv export` emits a single-marker, runtime-flat requirement set without macOS-only wheels.
The override-dependencies and `prerelease = "allow"` under `[tool.uv]` keep the resolution valid; for example, `lerobot==0.5.1` requires `torch<2.11`, so the lock pins the latest resolver-compatible Torch 2.10 series instead of the invalid Torch 2.12 output an unconstrained compile would produce.

Some pins are corrections, not regressions: `av<16` and `cmake<4.2` come from LeRobot's declared constraints. Dependabot regenerates `uv.lock` natively, and the read-only `uv lock --check` CI gate fails any PR whose lock drifts from `pyproject.toml`.

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

## 🤖 GR00T VLA Fine-Tuning

NVIDIA Isaac-GR00T (N1.5 and N1.7) is a vision-language-action foundation model for robot manipulation. Fine-tuning uses a dedicated VLA submission script and workflow (`training/vla/workflows/osmo/groot-train.yaml`); `--vla-version` selects the GR00T codebase ref and the matching config injection path.

| Version | Default base model     | Config injection                                             |
|---------|------------------------|--------------------------------------------------------------|
| N1.5    | `nvidia/GR00T-N1.5-3B` | `--data-config-file` appended to `data_config.py`            |
| N1.7    | `nvidia/GR00T-N1.7-3B` | `--modality-config-file` loaded via `--modality_config_path` |

Reference templates for both versions live in [`training/vla/configs/groot/examples/`](https://github.com/microsoft/physical-ai-toolchain/blob/main/training/vla/configs/groot/examples/README.md).

### Quick Start — GR00T-N1.5

```bash
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --vla-version 1.5 \
  --base-model nvidia/GR00T-N1.5-3B \
  --data-config example \
  --data-config-file training/vla/configs/groot/examples/data_config.py \
  --blob-url https://myaccount.blob.core.windows.net/datasets/my-data
```

### Quick Start — GR00T-N1.7

```bash
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --vla-version 1.7 \
  --base-model nvidia/GR00T-N1.7-3B \
  --data-config example \
  --modality-config-file training/vla/configs/groot/examples/modality_config.py \
  --blob-url https://myaccount.blob.core.windows.net/datasets/my-data
```

When `--vla-version 1.7` is set the script auto-resolves `${name}_modality_config.py` from `training/vla/configs/groot/` (or `examples/modality_config.py` when `--data-config example`); pass `--modality-config-file` explicitly to override.

### GR00T Configuration

| Parameter                | Default                                        | Description                                                |
|--------------------------|------------------------------------------------|------------------------------------------------------------|
| `--blob-url`             | (required)                                     | Full Azure Blob URL to LeRobot dataset                     |
| `--vla-version`          | `1.5`                                          | GR00T codebase: `1.5` or `1.7`                             |
| `--base-model`           | `nvidia/GR00T-N1.5-3B` (1.5) / `N1.7-3B` (1.7) | Base model for fine-tuning                                 |
| `--data-config`          | (required)                                     | Data config key mapping dataset modalities to model inputs |
| `--data-config-file`     | auto-resolved from `--data-config`             | N1.5 path: Python class appended to `data_config.py`       |
| `--modality-config-file` | auto-resolved from `--data-config` (1.7 only)  | N1.7 path: Python `ModalityConfig` loaded at launch        |
| `--embodiment-tag`       | `new_embodiment`                               | Embodiment identifier for custom robots                    |
| `--groot-ref`            | auto-selected per `--vla-version`              | Isaac-GR00T git commit ref                                 |
| `--max-steps`            | `500`                                          | Max training steps                                         |
| `--batch-size`           | `4`                                            | Training batch size                                        |
| `--save-steps`           | `100`                                          | Checkpoint save frequency                                  |
| `--dataloader-workers`   | `0`                                            | Dataloader worker threads                                  |
| `--platform`             | `h100gpu_platform`                             | OSMO platform (GPU pool)                                   |
| `--resume`               |                                                | Resume from latest checkpoint                              |
| `--run-id-override`      |                                                | Resume a specific run by ID                                |
| `--azure-upload`         |                                                | Mirror checkpoint to Azure ML                              |
| `--azureml-model-name`   | `groot-model`                                  | Model name in Azure ML registry                            |
| `--acr-registry`         |                                                | Push checkpoint to ACR as OCI artifact                     |
| `--image`                | `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel`  | Container image                                            |

### GR00T vs ACT/Diffusion

| Aspect          | ACT/Diffusion                           | GR00T (N1.5 / N1.7)                    |
|-----------------|-----------------------------------------|----------------------------------------|
| Dataset source  | HuggingFace Hub or Blob via prefix      | Azure Blob URL (full path)             |
| Payload         | Base64-encoded training scripts         | Self-contained workflow (clones GR00T) |
| Container image | `pytorch:2.4.1-cuda12.4-cudnn9-runtime` | `pytorch:2.6.0-cuda12.4-cudnn9-devel`  |
| GPU requirement | Standard                                | H100 recommended (200Gi ephemeral)     |
| Logging         | MLflow (real-time)                      | TensorBoard + optional Azure ML mirror |
| Resume          | Not supported                           | `--resume --run-id-override <id>`      |

### Azure ML Checkpoint Mirror

Upload the final checkpoint and TensorBoard logs to Azure ML after training (works with either `--vla-version`):

```bash
./training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh \
  --vla-version 1.7 \
  --base-model nvidia/GR00T-N1.7-3B \
  --data-config example \
  --modality-config-file training/vla/configs/groot/examples/modality_config.py \
  --blob-url https://myaccount.blob.core.windows.net/datasets/my-data \
  --azure-upload \
  --azureml-model-name my-groot-model \
  --azure-subscription-id <subscription-id> \
  --azure-resource-group <resource-group> \
  --azure-workspace-name <workspace-name>
```

Azure ML mirror uses `DefaultAzureCredential` (workload identity on AKS). The checkpoint is registered as a new version of the model in the Azure ML model registry.

### Custom Embodiment Data Configs

GR00T requires a data config that maps dataset modalities (video keys, state keys, action keys) to the model's input format. Isaac-GR00T includes built-in configs (e.g., `gr1`, `so100`). Custom configs are injected at runtime via `--data-config-b64`, which base64-encodes a Python class and appends it to `data_config.py` in the Isaac-GR00T repo.

Reference templates live in [`training/vla/configs/groot/examples/`](https://github.com/microsoft/physical-ai-toolchain/blob/main/training/vla/configs/groot/examples/README.md). Copy `data_config.py` (and `modality_config.py` for N1.7+) into `training/vla/configs/groot/` as `<embodiment>_data_config.py`, adapt the keys to your dataset's `meta/modality.json`, and pass `--data-config <embodiment>` — the submission script auto-resolves the matching file.

## 🔗 Related Documentation

- [Experiment Tracking](experiment-tracking.md) for MLflow configuration
- [AzureML Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/azureml/README.md) for job template reference
- [OSMO Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/osmo/README.md) for workflow template reference
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
