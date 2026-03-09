---
title: Script Examples
description: Detailed submission examples for OSMO dataset training, LeRobot behavioral cloning, inference evaluation, AzureML training, and end-to-end pipelines.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-08
ms.topic: reference
keywords:
  - examples
  - training
  - inference
  - lerobot
  - osmo
  - azureml
  - pipeline
---

Detailed submission examples for training, inference, and pipeline workflows on OSMO and Azure ML platforms.

> [!NOTE]
> For CLI argument reference and script inventory, see [Script Reference](scripts.md).

## OSMO Dataset Training

The `submit-osmo-dataset-training.sh` script uploads `src/training/` as a versioned OSMO dataset. This approach removes the ~1MB size limit of base64-encoded archives and enables dataset reuse across runs.

### Dataset Submission Example

```bash
# Default dataset configuration
./submit-osmo-dataset-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# Custom dataset bucket and name
./submit-osmo-dataset-training.sh \
  --dataset-bucket custom-bucket \
  --dataset-name my-training-v1 \
  --task Isaac-Velocity-Rough-Anymal-C-v0

# With checkpoint resume
./submit-osmo-dataset-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --checkpoint-uri "runs:/abc123/checkpoint" \
  --checkpoint-mode resume
```

### Dataset Parameters

| Parameter          | Default         | Description                   |
|--------------------|-----------------|-------------------------------|
| `--dataset-bucket` | `training`      | OSMO bucket for training code |
| `--dataset-name`   | `training-code` | Dataset name (auto-versioned) |
| `--training-path`  | `src/training`  | Local folder to upload        |

The script stages files to exclude `__pycache__` and build artifacts via `.amlignore` patterns before upload.

## LeRobot Behavioral Cloning

The `submit-osmo-lerobot-training.sh` script submits LeRobot training workflows supporting ACT and Diffusion policy architectures. Uses HuggingFace Hub datasets and installs runtime dependencies from `src/training/lerobot/pyproject.toml`.

### LeRobot Submission Examples

```bash
# ACT policy with WANDB logging
./submit-osmo-lerobot-training.sh -d user/my-dataset

# Diffusion policy with Azure MLflow
./submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  -p diffusion \
  --mlflow-enable \
  -r my-model-name

# Fine-tune from pre-trained policy
./submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  --policy-repo-id user/pretrained-act \
  --training-steps 50000 \
  --batch-size 16
```

### LeRobot Parameters

| Parameter           | Default                | Description                        |
|---------------------|------------------------|------------------------------------|
| `--dataset-repo-id` | (required)             | HuggingFace dataset repository ID  |
| `--policy-type`     | `act`                  | Policy: `act`, `diffusion`         |
| `--job-name`        | `lerobot-act-training` | Job identifier                     |
| `--wandb-enable`    | enabled                | WANDB logging (default)            |
| `--mlflow-enable`   | disabled               | Azure ML MLflow logging            |
| `--policy-repo-id`  | (none)                 | Pre-trained policy for fine-tuning |
| `--training-steps`  | (LeRobot default)      | Total training iterations          |
| `--save-freq`       | `5000`                 | Checkpoint save frequency          |

## LeRobot Inference

The `submit-osmo-lerobot-inference.sh` script evaluates trained LeRobot policies from HuggingFace Hub. Downloads the policy, runs evaluation, and optionally registers the model to Azure ML.

### LeRobot Inference Examples

```bash
# Evaluate a trained policy
./submit-osmo-lerobot-inference.sh --policy-repo-id user/trained-act-policy

# Evaluate with model registration
./submit-osmo-lerobot-inference.sh \
  --policy-repo-id user/trained-act-policy \
  -r my-evaluated-model

# Diffusion policy evaluation
./submit-osmo-lerobot-inference.sh \
  --policy-repo-id user/trained-diffusion \
  -p diffusion \
  --eval-episodes 50
```

### Inference Parameters

| Parameter           | Default    | Description                          |
|---------------------|------------|--------------------------------------|
| `--policy-repo-id`  | (required) | HuggingFace policy repository        |
| `--policy-type`     | `act`      | Policy: `act`, `diffusion`           |
| `--eval-episodes`   | `10`       | Number of evaluation episodes        |
| `--register-model`  | (none)     | Model name for Azure ML registration |
| `--dataset-repo-id` | (none)     | Dataset for environment replay       |

## AzureML LeRobot Training

The `submit-azureml-lerobot-training.sh` script submits LeRobot training directly to Azure ML instead of OSMO. It registers an environment, compiles runtime dependencies from `src/training/lerobot/pyproject.toml`, and submits via `az ml job create`.

### AzureML LeRobot Examples

```bash
# ACT policy training
./submit-azureml-lerobot-training.sh -d user/my-dataset

# With model registration and log streaming
./submit-azureml-lerobot-training.sh \
  -d user/my-dataset \
  -r my-act-model \
  --stream

# Custom environment and compute
./submit-azureml-lerobot-training.sh \
  -d user/my-dataset \
  --image custom-registry.io/lerobot:latest \
  --compute my-gpu-cluster
```

## End-to-End Pipeline

The `run-lerobot-pipeline.sh` script orchestrates the full LeRobot lifecycle: training → polling → inference → model registration. It delegates to the individual submission scripts and polls OSMO workflow status between stages.

### Pipeline Stages

| Stage | Action                                | Script Used                        |
|-------|---------------------------------------|------------------------------------|
| 1     | Submit training workflow              | `submit-osmo-lerobot-training.sh`  |
| 2     | Poll workflow status until completion | `osmo workflow status`             |
| 3     | Submit inference/evaluation workflow  | `submit-osmo-lerobot-inference.sh` |

### Pipeline Examples

```bash
# Full pipeline: train → evaluate → register
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id user/my-act-policy \
  -r my-act-model

# Async mode (submit training and exit)
./run-lerobot-pipeline.sh \
  -d user/my-dataset \
  --skip-wait

# Diffusion pipeline with MLflow
./run-lerobot-pipeline.sh \
  -d user/my-dataset \
  --policy-repo-id user/my-diffusion \
  -p diffusion \
  --mlflow-enable \
  --training-steps 100000 \
  -r my-diffusion-model

# Skip inference (training only with polling)
./run-lerobot-pipeline.sh \
  -d user/my-dataset \
  --skip-inference
```

### Pipeline Parameters

| Parameter           | Default     | Description                      |
|---------------------|-------------|----------------------------------|
| `--dataset-repo-id` | (required)  | HuggingFace dataset repository   |
| `--policy-repo-id`  | (required*) | HuggingFace policy target repo   |
| `--policy-type`     | `act`       | Policy: `act`, `diffusion`       |
| `--register-model`  | (none)      | Azure ML model registration name |
| `--poll-interval`   | `60`        | Status check interval (seconds)  |
| `--timeout`         | `720`       | Training timeout (minutes)       |
| `--skip-wait`       | disabled    | Async mode: submit and exit      |
| `--skip-inference`  | disabled    | Skip inference stage             |

## Related Documentation

- [Script Reference](scripts.md) for CLI arguments and script inventory
- [Reference Hub](README.md) for all reference documentation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
