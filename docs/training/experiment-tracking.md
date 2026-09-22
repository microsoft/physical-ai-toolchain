---
sidebar_position: 3
title: Experiment Tracking
description: MLflow experiment tracking configuration for training workflows on Azure ML and OSMO
author: Microsoft Robotics-AI Team
ms.date: 2026-09-19
ms.topic: how-to
keywords:
  - mlflow
  - experiment tracking
  - model registration
  - checkpoints
---

Experiment tracking for Isaac Lab and LeRobot training workflows. Azure ML provides managed MLflow tracking on both platforms (Azure ML directly, OSMO via the Azure ML backend).

## 📊 MLflow Tracking

Azure ML manages MLflow as the default experiment tracking backend. Isaac Lab training with SKRL logs metrics automatically through monkey-patching.

### Isaac Lab (Automatic)

With MLflow configured, SKRL training logs the metrics exposed by the selected agent, including available episode rewards, training losses, optimization stats, and timing data. Metric availability varies by algorithm and run.

Configure logging frequency through the launcher in a configured Isaac Lab runtime, from the repository root:

```bash
bash training/rl/scripts/train.sh \
  --task Isaac-Cartpole-v0 \
  --headless \
  --mlflow_log_interval balanced
```

The direct training argument is `--mlflow_log_interval`, not a submission-script flag:

| Interval   | Behavior                     | Use Case          |
|------------|------------------------------|-------------------|
| `step`     | Log every training step      | Debugging         |
| `balanced` | Log every 10 steps (default) | Standard training |
| `rollout`  | Log once per rollout cycle   | Long runs         |
| Integer    | Custom step interval         | Tuned granularity |

See [MLflow Integration](mlflow-integration.md) for SKRL metric categories, filtering, and troubleshooting.

### LeRobot

MLflow is enabled automatically for LeRobot training on both OSMO and Azure ML. Submit an OSMO training job:

```bash
training/il/scripts/submit-osmo-lerobot-training.sh \
  -d user/dataset
```

### MLflow Configuration

| Parameter                | Default | Description                       | Source                                  |
|--------------------------|---------|-----------------------------------|-----------------------------------------|
| `--mlflow-token-retries` | `3`     | MLflow token refresh retry count  | `MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES` |
| `--mlflow-http-timeout`  | `60`    | MLflow HTTP request timeout (sec) | `MLFLOW_HTTP_REQUEST_TIMEOUT`           |

## Model Registration

Isaac Lab RL submitters enable checkpoint registration by default. LeRobot registration is opt-in through the applicable training, evaluation, or pipeline submitter; the flags are not interchangeable across families.

### Registration Parameters

| Parameter                                   | Default                                | Description                                                 |
|---------------------------------------------|----------------------------------------|-------------------------------------------------------------|
| `--register-checkpoint`                     | Derived from task (RL); none (LeRobot) | RL and LeRobot training submitters: registration name       |
| `--skip-register-checkpoint`                | `false`                                | RL submitters: skip registration                            |
| `--register-model`                          | (none)                                 | LeRobot evaluation submitters: registration name            |
| `--with-register` + `--register-model-name` | Disabled                               | AzureML LeRobot pipeline: enable registration step and name |

### Registration Examples

```bash
# Isaac Lab: custom model name
training/rl/scripts/submit-azureml-training.sh \
  --register-checkpoint my-anymal-model

# Isaac Lab: skip registration
training/rl/scripts/submit-osmo-training.sh \
  --skip-register-checkpoint

# LeRobot: register after evaluation
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --policy-repo-id user/trained-policy \
  --policy-revision "<policy-commit-sha>" \
  --dataset-repo-id user/evaluation-dataset \
  --dataset-revision "<dataset-commit-sha>" \
  -r my-evaluated-model
```

### Retrieve Registered Models

```bash
# Download from Azure ML
az ml model download \
  --name anymal-c-velocity --version 1 \
  --download-path ./checkpoint

# Download from HuggingFace Hub
huggingface-cli download user/trained-policy --local-dir ./checkpoint
```

## 🔄 Checkpoint Workflows

Isaac Lab RL training supports three checkpoint initialization modes:

| Mode           | Weights | Optimizer | Counters | Use Case                      |
|----------------|---------|-----------|----------|-------------------------------|
| `from-scratch` | Random  | Fresh     | Reset    | Initial training              |
| `warm-start`   | Loaded  | Fresh     | Reset    | Transfer learning             |
| `resume`       | Loaded  | Loaded    | Loaded   | Continue interrupted training |

```bash
# Resume training from MLflow artifact
training/rl/scripts/submit-azureml-training.sh \
  --checkpoint-uri "runs:/abc123/checkpoint" \
  --checkpoint-mode resume

# Warm-start from registered model
training/rl/scripts/submit-osmo-training.sh \
  --checkpoint-uri "models:/anymal-c-velocity/1" \
  --checkpoint-mode warm-start
```

## 🔗 Related Documentation

- [MLflow Integration](mlflow-integration.md) for SKRL metric logging internals
- [Isaac Lab Training](isaac-lab-training.md) for RL training workflows
- [LeRobot Training](lerobot-training.md) for behavioral cloning workflows
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
