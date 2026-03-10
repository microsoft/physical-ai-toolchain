---
title: Experiment Tracking
description: MLflow and WANDB experiment tracking configuration for training workflows on Azure ML and OSMO
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: how-to
keywords:
  - mlflow
  - wandb
  - experiment tracking
  - model registration
  - checkpoints
---

Experiment tracking for Isaac Lab and LeRobot training workflows. Azure ML provides managed MLflow tracking. OSMO supports both WANDB (default for LeRobot) and MLflow (via Azure ML backend).

## 📊 MLflow Tracking

Azure ML manages MLflow as the default experiment tracking backend. Isaac Lab training with SKRL logs metrics automatically through monkey-patching.

### Isaac Lab (Automatic)

SKRL training logs metrics to MLflow without additional configuration. Metrics include episode rewards, training losses, optimization stats, and timing data.

Configure logging frequency with `--mlflow_log_interval`:

| Interval   | Behavior                     | Use Case          |
|------------|------------------------------|-------------------|
| `step`     | Log every training step      | Debugging         |
| `balanced` | Log every 10 steps (default) | Standard training |
| `rollout`  | Log once per rollout cycle   | Long runs         |
| Integer    | Custom step interval         | Tuned granularity |

See [MLflow Integration](mlflow-integration.md) for SKRL metric categories, filtering, and troubleshooting.

### LeRobot

Enable MLflow for LeRobot on OSMO:

```bash
./scripts/submit-osmo-lerobot-training.sh \
  -d user/dataset \
  --mlflow-enable
```

Azure ML LeRobot submissions use MLflow automatically.

### MLflow Configuration

| Parameter                | Default | Description                       | Source                                  |
|--------------------------|---------|-----------------------------------|-----------------------------------------|
| `--mlflow-token-retries` | `3`     | MLflow token refresh retry count  | `MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES` |
| `--mlflow-http-timeout`  | `60`    | MLflow HTTP request timeout (sec) | `MLFLOW_HTTP_REQUEST_TIMEOUT`           |

## 📈 WANDB Integration

WANDB is the default experiment tracker for LeRobot workflows on OSMO. Tracks training loss, evaluation metrics, and model outputs.

### Credential Setup

```bash
# Set WANDB API key (required)
osmo credential set wandb_api_key --generic --value "..."

# Set HuggingFace token (required for private datasets)
osmo credential set hf_token --generic --value "hf_..."
```

### Enable and Disable

WANDB is enabled by default on OSMO LeRobot workflows:

```bash
# Explicitly enable (default)
./scripts/submit-osmo-lerobot-training.sh -d user/dataset --wandb-enable

# Disable WANDB
./scripts/submit-osmo-lerobot-training.sh -d user/dataset --wandb-disable

# Use MLflow instead
./scripts/submit-osmo-lerobot-training.sh -d user/dataset --mlflow-enable
```

## 📦 Model Registration

Training scripts register model checkpoints to Azure ML automatically at completion.

### Registration Parameters

| Parameter                    | Default           | Description                    |
|------------------------------|-------------------|--------------------------------|
| `--register-checkpoint`      | Derived from task | Model name for registration    |
| `--skip-register-checkpoint` | `false`           | Skip automatic registration    |
| `--register-model`           | (none)            | Model name (LeRobot inference) |

### Registration Examples

```bash
# Isaac Lab: custom model name
./scripts/submit-azureml-training.sh \
  --register-checkpoint my-anymal-model

# Isaac Lab: skip registration
./scripts/submit-osmo-training.sh \
  --skip-register-checkpoint

# LeRobot: register after inference
./scripts/submit-osmo-lerobot-inference.sh \
  --policy-repo-id user/trained-policy \
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

Training supports four checkpoint initialization modes:

| Mode           | Weights | Optimizer | Counters | Use Case                         |
|----------------|---------|-----------|----------|----------------------------------|
| `from-scratch` | Random  | Fresh     | Reset    | Initial training                 |
| `warm-start`   | Loaded  | Fresh     | Reset    | Transfer learning                |
| `resume`       | Loaded  | Loaded    | Loaded   | Continue interrupted training    |
| `fresh`        | Random  | Fresh     | Reset    | Architecture-only initialization |

```bash
# Resume training from MLflow artifact
./scripts/submit-azureml-training.sh \
  --checkpoint-uri "runs:/abc123/checkpoint" \
  --checkpoint-mode resume

# Warm-start from registered model
./scripts/submit-osmo-training.sh \
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
