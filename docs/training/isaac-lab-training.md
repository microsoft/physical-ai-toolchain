---
sidebar_position: 4
title: Isaac Lab Training
description: Reinforcement learning training with SKRL and RSL-RL backends on Azure ML and OSMO platforms
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: how-to
keywords:
  - isaac lab
  - skrl
  - rsl-rl
  - reinforcement learning
  - azureml
  - osmo
  - training
---

Isaac Lab reinforcement learning training with SKRL and RSL-RL backends. Both Azure ML and OSMO platforms support distributed GPU training with automatic checkpointing and MLflow experiment tracking.

## 📋 Prerequisites

| Component         | Requirement                                                                   |
|-------------------|-------------------------------------------------------------------------------|
| Infrastructure    | AKS cluster deployed via [Infrastructure Guide](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/README.md)       |
| Azure ML          | Extension installed via `02-deploy-azureml-extension.sh`                      |
| OSMO              | Control plane and backend via `03-deploy-osmo-control-plane.sh`               |
| Terraform outputs | Available in `infrastructure/terraform/` (or provide values via CLI / environment vars) |
| Azure CLI         | `az` with `ml` extension for Azure ML submissions                             |
| OSMO CLI          | `osmo` CLI installed and authenticated for OSMO submissions                   |

## 🚀 Quick Start

### Azure ML

```bash
./scripts/submit-azureml-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --num-envs 2048 \
  --stream
```

### OSMO (Base64 Payload)

```bash
./scripts/submit-osmo-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --num-envs 2048
```

### OSMO (Dataset Injection)

```bash
./scripts/submit-osmo-dataset-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --dataset-name my-training-v1
```

Dataset injection removes the ~1 MB payload size limit of base64-encoded archives and enables dataset reuse across runs.

## ⚖️ Platform Selection

| Aspect              | Azure ML                              | OSMO                                 |
|---------------------|---------------------------------------|--------------------------------------|
| Submission          | `az ml job create` via YAML templates | `osmo workflow submit`               |
| Orchestration       | AKS compute targets                   | KAI Scheduler / Volcano integration  |
| Experiment tracking | MLflow (managed)                      | MLflow (Azure ML backend)            |
| Dataset delivery    | Azure ML datastores                   | Base64 payload or OSMO bucket upload |
| Monitoring          | Azure ML Studio                       | OSMO UI Dashboard                    |
| Payload modes       | Single (YAML template)                | Base64 or dataset folder injection   |

Azure ML provides managed compute and experiment tracking through Azure ML Studio. OSMO adds distributed training coordination, KAI Scheduler integration, and a dataset versioning system.

## ⚙️ Training Configuration

Core parameters shared across platforms:

| Parameter          | Default                            | Description                          |
|--------------------|------------------------------------|--------------------------------------|
| `--task`           | `Isaac-Velocity-Rough-Anymal-C-v0` | Isaac Lab task identifier            |
| `--num-envs`       | `2048`                             | Parallel simulation environments     |
| `--max-iterations` | (unset)                            | Training iteration limit             |
| `--image`          | `nvcr.io/nvidia/isaac-lab:2.2.0`   | Container image                      |
| `--backend`        | `skrl`                             | Training backend: `skrl` or `rsl_rl` |
| `--headless`       | `true`                             | Disable rendering                    |

Values resolve in order: CLI arguments → environment variables → Terraform outputs.

### Training Backends

| Backend | Algorithms                 | Use Case                            |
|---------|----------------------------|-------------------------------------|
| SKRL    | PPO, IPPO, MAPPO, AMP, SAC | General-purpose RL with MLflow      |
| RSL-RL  | PPO, Distillation          | Locomotion-focused, teacher-student |

SKRL is the default backend and supports automatic MLflow metric logging via monkey-patching. See [MLflow Integration](mlflow-integration.md) for metric details.

## 🔄 Checkpoint Workflows

Four checkpoint modes control how training initializes:

| Mode           | Behavior                                                    |
|----------------|-------------------------------------------------------------|
| `from-scratch` | Default. No checkpoint loaded, training starts fresh.       |
| `warm-start`   | Load weights only. Resets optimizer and iteration counters. |
| `resume`       | Load full state. Continues from exact training position.    |
| `fresh`        | Load model architecture only. Reinitializes all parameters. |

### Checkpoint Examples

```bash
# Resume interrupted training (Azure ML)
./scripts/submit-azureml-training.sh \
  --checkpoint-uri "runs:/abc123/checkpoint" \
  --checkpoint-mode resume

# Warm-start from a registered model (OSMO)
./scripts/submit-osmo-training.sh \
  --checkpoint-uri "models:/anymal-c-velocity/1" \
  --checkpoint-mode warm-start
```

### Model Registration

Training scripts register checkpoints to Azure ML automatically. Override the model name or skip registration:

```bash
# Custom model name
./scripts/submit-azureml-training.sh \
  --register-checkpoint my-custom-model

# Skip registration
./scripts/submit-osmo-training.sh \
  --skip-register-checkpoint
```

## 💾 Dataset Injection (OSMO)

OSMO supports two payload delivery modes for training code:

| Mode              | Script                            | Size Limit | Versioning |
|-------------------|-----------------------------------|------------|------------|
| Base64 payload    | `submit-osmo-training.sh`         | ~1 MB      | None       |
| Dataset injection | `submit-osmo-dataset-training.sh` | Unlimited  | Automatic  |

Dataset injection uploads `training/rl/` as a versioned OSMO dataset, mounted at `/data/<dataset_name>/training` in the container:

```bash
./scripts/submit-osmo-dataset-training.sh \
  --dataset-bucket custom-bucket \
  --dataset-name my-training-v1 \
  --task Isaac-Velocity-Rough-Anymal-C-v0
```

The script stages files to exclude `__pycache__` and build artifacts via `.amlignore` patterns before upload.

## 🔗 Related Documentation

- [Experiment Tracking](experiment-tracking.md) for MLflow and WANDB setup
- [MLflow Integration](mlflow-integration.md) for SKRL metric logging internals
- [AzureML Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/azureml/README.md) for job template reference
- [OSMO Workflows](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/osmo/README.md) for workflow template reference
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
