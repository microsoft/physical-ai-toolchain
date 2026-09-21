---
sidebar_position: 4
title: Script Examples
description: Detailed submission examples for OSMO dataset training, LeRobot behavioral cloning, inference evaluation, AzureML training, and end-to-end pipelines.
author: Microsoft Robotics-AI Team
ms.date: 2026-09-19
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

The `training/rl/scripts/submit-osmo-dataset-training.sh` script uploads `training/rl/` as a versioned OSMO dataset and enables dataset reuse across runs. Run the examples from the repository root.

### Dataset Submission Example

```bash
# Default dataset configuration
./training/rl/scripts/submit-osmo-dataset-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# Custom dataset bucket and name
./training/rl/scripts/submit-osmo-dataset-training.sh \
  --dataset-bucket custom-bucket \
  --dataset-name my-training-v1 \
  --task Isaac-Velocity-Rough-Anymal-C-v0

# With checkpoint resume
./training/rl/scripts/submit-osmo-dataset-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --checkpoint-uri "runs:/abc123/checkpoint" \
  --checkpoint-mode resume
```

### Dataset Parameters

| Parameter          | Default         | Description                   |
|--------------------|-----------------|-------------------------------|
| `--dataset-bucket` | `training`      | OSMO bucket for training code |
| `--dataset-name`   | `training-code` | Dataset name (auto-versioned) |
| `--training-path`  | `training/rl`   | Local folder to upload        |

The script stages files to exclude `__pycache__` and build artifacts via `.amlignore` patterns before upload.

## LeRobot Behavioral Cloning

The `training/il/scripts/submit-osmo-lerobot-training.sh` script submits LeRobot training workflows supporting ACT and Diffusion policy architectures. It trains from HuggingFace Hub datasets or Azure Blob datasets. The dependency contract is `training/il/pyproject.toml` with its committed `training/il/uv.lock`; runtime installation must preserve the project's configured package sources.

### LeRobot Submission Examples

```bash
# ACT policy with default MLflow tracking
./training/il/scripts/submit-osmo-lerobot-training.sh -d user/my-dataset

# Diffusion policy with Azure MLflow
./training/il/scripts/submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  -p diffusion \
  -r my-model-name

# Train from Azure Blob Storage
./training/il/scripts/submit-osmo-lerobot-training.sh \
  --blob-url https://account.blob.core.windows.net/datasets/pusht \
  -r pusht-model

# Fine-tune from pre-trained policy
./training/il/scripts/submit-osmo-lerobot-training.sh \
  -d user/my-dataset \
  --policy-repo-id user/pretrained-act \
  --training-steps 50000 \
  --batch-size 16
```

### LeRobot Parameters

| Parameter           | Default                                              | Description                                                     |
|---------------------|------------------------------------------------------|-----------------------------------------------------------------|
| `--dataset-repo-id` | Required for HuggingFace; `dataset` for Blob sources | HuggingFace dataset repository ID or logical local dataset name |
| `--blob-url`        | (none)                                               | Direct Azure Blob dataset URL; repeatable                       |
| `--policy-type`     | `act`                                                | Policy: `act`, `diffusion`                                      |
| `--job-name`        | `lerobot-act-training`                               | Job identifier                                                  |
| `--policy-repo-id`  | (none)                                               | Pre-trained policy for fine-tuning                              |
| `--training-steps`  | `100000`                                             | Total training iterations                                       |
| `--batch-size`      | `32`                                                 | Training batch size                                             |
| `--learning-rate`   | `1e-4`                                               | Optimizer learning rate                                         |
| `--save-freq`       | `5000`                                               | Checkpoint save frequency                                       |

## LeRobot Inference

The `evaluation/sil/scripts/submit-osmo-lerobot-eval.sh` script evaluates trained LeRobot policies and optionally registers the model to Azure ML. Hub replay evaluation requires both a policy and a dataset, each with an immutable commit revision. Replace the quoted revision placeholders before submission.

### LeRobot Inference Examples

```bash
# Evaluate a trained policy
./evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --policy-repo-id user/trained-act-policy \
  --policy-revision "<policy-commit-sha>" \
  --dataset-repo-id user/evaluation-dataset \
  --dataset-revision "<dataset-commit-sha>"

# Evaluate with model registration
./evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --policy-repo-id user/trained-act-policy \
  --policy-revision "<policy-commit-sha>" \
  --dataset-repo-id user/evaluation-dataset \
  --dataset-revision "<dataset-commit-sha>" \
  -r my-evaluated-model

# Diffusion policy evaluation
./evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --policy-repo-id user/trained-diffusion \
  --policy-revision "<policy-commit-sha>" \
  --dataset-repo-id user/evaluation-dataset \
  --dataset-revision "<dataset-commit-sha>" \
  -p diffusion \
  --eval-episodes 50
```

### Inference Parameters

| Parameter            | Default            | Description                          |
|----------------------|--------------------|--------------------------------------|
| `--policy-repo-id`   | (required)         | HuggingFace policy repository        |
| `--policy-type`      | `act`              | Policy: `act`, `diffusion`           |
| `--eval-episodes`    | `10`               | Number of evaluation episodes        |
| `--register-model`   | (none)             | Model name for Azure ML registration |
| `--dataset-repo-id`  | (required for Hub) | Dataset for environment replay       |
| `--policy-revision`  | (required for Hub) | Immutable policy commit SHA          |
| `--dataset-revision` | (required for Hub) | Immutable dataset commit SHA         |

## AzureML LeRobot Training

The `training/il/scripts/submit-azureml-lerobot-training.sh` script submits LeRobot training directly to Azure ML instead of OSMO. It registers an environment and submits via `az ml job create`. Runtime dependencies follow `training/il/pyproject.toml` and its committed `uv.lock`, not a separate `lerobot/` subproject.

### AzureML LeRobot Examples

```bash
# ACT policy training
./training/il/scripts/submit-azureml-lerobot-training.sh -d user/my-dataset

# With model registration and log streaming
./training/il/scripts/submit-azureml-lerobot-training.sh \
  -d user/my-dataset \
  -r my-act-model \
  --stream

# Custom compute with the checked-in digest-pinned image default
./training/il/scripts/submit-azureml-lerobot-training.sh \
  -d user/my-dataset \
  --compute my-gpu-cluster
```

## End-to-End Pipeline

The `training/pipelines/run-lerobot-pipeline.sh` wrapper is intended to orchestrate training, polling, evaluation, and registration on OSMO. Its evaluation stage still calls the missing `scripts/submit-osmo-lerobot-inference.sh`; it is not a working end-to-end path. Submit training and evaluation with the individual scripts above until the wrapper is repaired.

### Pipeline Stages

| Stage | Action                                | Script Used                                           |
|-------|---------------------------------------|-------------------------------------------------------|
| 1     | Submit training workflow              | `training/il/scripts/submit-osmo-lerobot-training.sh` |
| 2     | Poll workflow status until completion | `osmo workflow query`                                 |
| 3     | Blocked: internal target missing      | `scripts/submit-osmo-lerobot-inference.sh`            |

### Pipeline Examples

```bash
# Async mode (submit training and exit)
./training/pipelines/run-lerobot-pipeline.sh \
  -d user/my-dataset \
  --skip-wait

# Skip inference (training only with polling)
./training/pipelines/run-lerobot-pipeline.sh \
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

- [AzureML Workflow Templates](workflow-templates-azureml.md) for the separate AzureML preprocess/train/evaluate pipeline and optional registration step
- [Script Reference](scripts.md) for CLI arguments and script inventory
- [Reference Hub](README.md) for all reference documentation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
