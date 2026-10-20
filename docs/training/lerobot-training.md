---
sidebar_position: 5
title: LeRobot Training
description: Behavioral cloning with ACT and Diffusion policies and SmolVLA fine-tuning on Azure ML, OSMO, and the local OSMO profile
author: Microsoft Robotics-AI Team
ms.date: 2026-10-19
ms.topic: how-to
keywords:
  - lerobot
  - behavioral cloning
  - act
  - diffusion
  - smolvla
  - azureml
  - osmo
  - training
---

LeRobot behavioral cloning training for ACT and Diffusion policy architectures, plus SmolVLA fine-tuning on OSMO. Training runs on Azure ML and OSMO platforms using HuggingFace Hub, Azure Blob, and AzureML data asset sources (Azure ML only), or local datasets on a workstation running the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md), with MLflow experiment tracking.

## 📋 Prerequisites

| Component         | Requirement                                                                                                                                                                                                                                                                    |
|-------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Infrastructure    | AKS cluster deployed via [Infrastructure Guide](https://github.com/microsoft/physical-ai-toolchain/blob/main/deploy/README.md), or a workstation running the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md) |
| Azure ML or OSMO  | At least one platform configured (see Platform Selection section)                                                                                                                                                                                                              |
| NVIDIA driver     | Release 580 or newer for the CUDA 13.0 LeRobot runtime images                                                                                                                                                                                                                  |
| HuggingFace token | Required only for private HuggingFace datasets (`hf_token` credential); Azure Blob and Data Asset sources use managed identity                                                                                                                                                 |

## 🚀 Quick Start

Run the commands in this guide from the repository root.

### Azure ML

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### OSMO

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### Local Workstation (OSMO)

Train from a dataset in the workstation's datasets directory and track the run in the local MLflow server:

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  --profile local \
  --local-dataset my-learner-dataset \
  -r my-act-policy
```

See [Local Datasets](#local-datasets-osmo-local-profile) for preparing datasets.

### End-to-End Pipeline (OSMO)

Train, evaluate, and register in one command:

```bash
./training/pipelines/run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id user/my-act-policy \
  -r my-act-model
```

## 🧠 Policy Architectures

| Architecture | Type                               | Strengths                                 |
|--------------|------------------------------------|-------------------------------------------|
| ACT          | Action Chunking with Transformers  | Multi-step prediction, temporal coherence |
| Diffusion    | Denoising Diffusion Policy         | Multi-modal action distributions          |
| SmolVLA      | Vision-Language-Action, fine-tuned | Compact, language-conditioned, one GPU    |
| GR00T-N1.5   | Vision-Language-Action Foundation  | Multi-embodiment, language-conditioned    |
| GR00T-N1.7   | Vision-Language-Action Foundation  | N1.5 + improved modality config pipeline  |

Select the architecture with `--policy-type`:

```bash
# ACT policy (default)
./training/il/scripts/submit-osmo-lerobot-training.sh -d user/dataset -p act

# Diffusion policy
./training/il/scripts/submit-osmo-lerobot-training.sh -d user/dataset -p diffusion

# SmolVLA fine-tuning (OSMO)
./training/il/scripts/submit-osmo-lerobot-training.sh -d user/dataset -p smolvla

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

| Aspect              | Azure ML                                            | OSMO                                                                       |
|---------------------|-----------------------------------------------------|----------------------------------------------------------------------------|
| Submission          | `az ml job create`                                  | `osmo workflow submit`                                                     |
| Experiment tracking | MLflow (managed)                                    | MLflow (Azure ML backend, or the local MLflow server on the local profile) |
| Credential handling | Azure ML environment variables                      | `osmo credential set` injection                                            |
| Dataset delivery    | HuggingFace Hub, Azure Blob, or AzureML data assets | HuggingFace Hub, direct Azure Blob URLs, or local datasets (local profile) |
| Pipeline support    | Manual multi-step                                   | `run-lerobot-pipeline.sh` orchestration                                    |

## ⚙️ Training Configuration

| Parameter                  | Default                                                                    | Description                                                                                                     |
|----------------------------|----------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| `--dataset-repo-id`        | Required for HuggingFace; `dataset` for Blob sources                       | HuggingFace dataset repository or logical local dataset name                                                    |
| `--blob-url`               | (none)                                                                     | Direct Azure Blob dataset URL; repeat for multiple sources                                                      |
| `--local-dataset`          | (none)                                                                     | Dataset folder in the profile's datasets directory, mounted read-only; OSMO local profile                       |
| `--policy-type`            | `act`                                                                      | Policy: `act`, `diffusion`, or `smolvla` (OSMO); GR00T uses its own submission script                           |
| `--job-name`               | `lerobot-<policy-type>-training` (OSMO), `lerobot-act-training` (Azure ML) | Job identifier                                                                                                  |
| `--image`                  | `pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime`                           | Container image                                                                                                 |
| `--training-steps`         | `100000`                                                                   | Total training iterations                                                                                       |
| `--batch-size`             | `32`                                                                       | Training batch size                                                                                             |
| `--save-freq`              | `5000`                                                                     | Checkpoint save frequency                                                                                       |
| `--policy-repo-id`         | (none)                                                                     | Repository ID recorded on the trained policy; Hub push stays off                                                |
| `--policy-path`            | `lerobot/smolvla_base` for `smolvla`                                       | Pretrained policy to fine-tune: Hub repository or container path; OSMO only                                     |
| `--policy-path-revision`   | Pinned base commit for `smolvla`                                           | Commit SHA that pins a Hub `--policy-path`; required for Hub repositories                                       |
| `--rename-map`             | (none)                                                                     | JSON map from dataset feature keys to the pretrained policy's keys; OSMO only                                   |
| `--init-from-policy-model` | (none)                                                                     | Warm-start from a registered AzureML model (`azureml:NAME:VERSION`); AzureML only                               |
| `--num-gpus`               | `1`                                                                        | GPUs requested per task; enables Accelerate multi-GPU launch when >1; OSMO only                                 |
| `--mixed-precision`        | `no`                                                                       | Accelerate mixed-precision mode (`no`/`fp16`/`bf16`); effective with more than one visible GPU                  |
| `--platform`               | `gpu_platform`                                                             | OSMO platform binding the GPU node pool; use `gpu_platform_2x` for 2x A100 nodes with `--num-gpus 2`; OSMO only |

### Multi-GPU Training (OSMO)

Single-node multi-GPU training launches `accelerate` with one process per visible GPU. Request the GPU count with `--num-gpus` and select a `--platform` whose node pool exposes at least that many GPUs:

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  --num-gpus 2 \
  --platform gpu_platform_2x \
  --mixed-precision bf16
```

The cluster must provide a node pool with a multi-GPU SKU and a matching OSMO platform; the shipped `gpu_platform_2x` binds a 2x A100 pool. Mixed precision only takes effect when more than one GPU is visible. See [Manage Node Pools](../infrastructure/manage-node-pools.md) to add multi-GPU pools and platforms.

### Fine-Tuning from Existing Policy

Pass the pretrained policy as `--policy-path`. A Hub repository needs the commit SHA that pins it, because LeRobot's `--policy.path` takes no revision; the job downloads that snapshot and fine-tunes from it. A path inside the container needs no revision.

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  --policy-path user/pretrained-act \
  --policy-path-revision "<policy-commit-sha>" \
  --training-steps 50000 \
  --batch-size 16
```

### SmolVLA Fine-Tuning (OSMO)

`-p smolvla` fine-tunes [`lerobot/smolvla_base`](https://huggingface.co/lerobot/smolvla_base) at the commit pinned in `scripts/lib/common.sh` unless `--policy-path` names another checkpoint. The job installs LeRobot from `training/vla/lerobot/uv.lock`, which carries the `smolvla` extra.

The base model expects camera features named `observation.images.camera1` through `camera3`. Map the dataset's cameras onto those names with `--rename-map`; SmolVLA leaves out any camera the dataset lacks:

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  --profile local \
  --local-dataset my-learner-dataset \
  -p smolvla \
  --rename-map '{"observation.images.front": "observation.images.camera1", "observation.images.wrist": "observation.images.camera2"}' \
  --batch-size 8
```

The base model works with states and actions up to 32 dimensions and pads smaller ones.

### Checkpoint Compatibility

LeRobot checkpoints trained before 0.6 store normalization inside the model. LeRobot 0.6 requires external preprocessor and postprocessor artifacts. Migrate legacy checkpoints before using `--policy-repo-id` or `--init-from-policy-model` with the LeRobot 0.6 runtime.

See [Migrate LeRobot Checkpoints](lerobot-checkpoint-migration.md) for the conversion and validation procedure.

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

The registered model must contain `config.json`, `model.safetensors`, both processor JSON files, and their processor tensor files. Register migrated checkpoints as new immutable model versions rather than replacing legacy artifacts.

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

Both submission scripts pin `policy.push_to_hub=false`. Checkpoints are registered to the tracking backend's model registry when `--register-checkpoint NAME` is passed; HuggingFace Hub upload is never used. The local profile logs runs and registers checkpoints in its in-cluster MLflow server.

`TRACKING_BACKEND` in the job environment can redirect runs and registrations to a self-hosted MLflow server (`mlflow`) or turn them off (`none`). See [Experiment Tracking](experiment-tracking.md) for the backends, platform comparison, and configuration details.

## 💾 Dataset Workflows

Supported dataset delivery differs by platform.

| Platform | Supported sources                                                                                     |
|----------|-------------------------------------------------------------------------------------------------------|
| OSMO     | HuggingFace Hub, Azure Blob, or local datasets on the local profile                                   |
| Azure ML | HuggingFace Hub, direct Azure Blob URLs, AzureML data assets, or combined Blob and data asset sources |

### HuggingFace Hub (Default)

LeRobot downloads datasets from HuggingFace Hub at runtime. Specify datasets with `--dataset-repo-id`:

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  -d lerobot/aloha_sim_insertion_human
```

### Azure Blob Storage (OSMO)

Train from direct Azure Blob URLs using OSMO workload identity. Blob submissions do not require a HuggingFace dataset repository; the script defaults the local dataset ID to `dataset`.

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  --blob-url "https://mystorageaccount.blob.core.windows.net/training/pusht" \
  -r pusht-model
```

Use multiple `--blob-url` values to merge compatible datasets before training:

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  --blob-url "https://account1.blob.core.windows.net/train/set1" \
  --blob-url "https://account2.blob.core.windows.net/train/set2" \
  -r merged-pusht-model
```

> [!IMPORTANT]
> OSMO accepts plain HTTPS Azure Blob URLs only. OSMO authenticates with its workload identity; grant that identity `Storage Blob Data Reader`, `Storage Blob Data Contributor`, or `Storage Blob Data Owner` on each storage account or container. AzureML data asset identifiers, datastore URIs, ADLS Gen2 URLs, Azure Files, OneLake, local paths, fragments, and any query string (including SAS tokens) are rejected.

### Local Datasets (OSMO, Local Profile)

On the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md), every task mounts the workstation's datasets directory read-only at `/mnt/datasets`. Pass a dataset folder name with `--local-dataset`, and the job trains from `/mnt/datasets/<name>` without a download:

```bash
./training/il/scripts/submit-osmo-lerobot-training.sh \
  --profile local \
  --local-dataset my-learner-dataset
```

Prepare the folder with [infrastructure/local/datasets.sh](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/datasets.sh), which imports a dataset or derives a learner dataset from validated captures without their privileged features. Checkpoints record the dataset as local lineage in the tracking backend.

### Azure Blob Storage (AzureML)

Train directly from Azure Blob Storage datasets using managed identity authentication. Supports single or multiple datasets with automatic merging.

#### Single Dataset

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --blob-url "https://mystorageaccount.blob.core.windows.net/training/pusht" \
  -r pusht-model
```

#### Multiple Datasets (Automatic Merge)

Combine datasets from different containers or storage accounts:

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --blob-url "https://account1.blob.core.windows.net/train/set1" \
  --blob-url "https://account1.blob.core.windows.net/train/set2" \
  -r merged-pusht-model
```

LeRobot automatically validates dataset compatibility and merges them before training.

### AzureML Data Asset (Native Mount, AzureML only)

Use registered AzureML data assets, mounted read-only into the training container via AzureML's native `ro_mount` mechanism. No download step is required — datasets are available immediately at a FUSE mount path.

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --dataset-asset azureml:pusht-episodes:3 \
  -r pusht-model
```

Multiple data assets can be merged:

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --dataset-asset azureml:episodes-day1:2 \
  --dataset-asset azureml:episodes-day2:1 \
  -r merged-model
```

The data asset URI must be version-pinned (`azureml:NAME:VERSION` or the full ARM path `azureml://.../data/NAME/versions/VERSION`). Shorthands like `@latest` are rejected to keep runs reproducible.

### Combined Sources (AzureML only)

Data assets and blob URLs can be combined. All sources are merged automatically via `lerobot-edit-dataset`:

```bash
./training/il/scripts/submit-azureml-lerobot-training.sh \
  --dataset-asset azureml:pusht-base:3 \
  --blob-url "https://account.blob.core.windows.net/extra/pusht" \
  -r combined-model
```

## 🔒 Runtime Dependency Lockfile

Azure ML and OSMO LeRobot jobs derive their runtime dependencies at build time from the committed `training/il/lerobot/uv.lock`, the single resolution source of truth. The entrypoints run `uv sync --active --frozen --no-config --no-install-project` against the LeRobot project, so the lock and its package-source mappings are the runtime contract. Regenerate the lock after any `training/il/lerobot/pyproject.toml` change:

```bash
cd training/il/lerobot
uv lock
```

`[tool.uv] environments` constrains the universal lock to Linux x86_64 and ARM64 targets. The IL, VLA, and evaluation locks resolve Torch 2.13 with Torchvision 0.28 from the official CUDA 13.0 index for Azure ML, OSMO, and NVIDIA Thor jobs, preserving the package source during runtime installation.
The `override-dependencies` and `prerelease = "allow"` settings under `[tool.uv]` keep the remaining Azure ML and LeRobot dependency exceptions explicit and reproducible.

Verify that GPU nodes use NVIDIA driver release 580 or newer before running the CUDA 13.0 images:

```bash
nvidia-smi --query-gpu=driver_version --format=csv,noheader
```

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
./training/pipelines/run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id user/my-policy \
  -r my-model

# Training only with polling (skip inference)
./training/pipelines/run-lerobot-pipeline.sh \
  -d user/dataset \
  --skip-inference

# Async mode (submit and exit)
./training/pipelines/run-lerobot-pipeline.sh \
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
| `--platform`             | `gpu_platform`                                 | OSMO platform (GPU pool)                                   |
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

- [Migrate LeRobot Checkpoints](lerobot-checkpoint-migration.md) for pre-0.6 checkpoint conversion
- [Experiment Tracking](experiment-tracking.md) for MLflow configuration
- [AzureML Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/azureml/README.md) for job template reference
- [OSMO Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/osmo/README.md) for workflow template reference
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
