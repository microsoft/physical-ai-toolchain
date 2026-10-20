---
sidebar_position: 3
title: Experiment Tracking
description: MLflow experiment tracking and checkpoint registration for training workflows on Azure ML, OSMO, and self-hosted MLflow
author: Microsoft Robotics-AI Team
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - mlflow
  - experiment tracking
  - model registration
  - checkpoints
---

Experiment tracking and checkpoint registration for Isaac Lab and LeRobot training. Jobs log to the Azure ML workspace's MLflow endpoint by default, or to a self-hosted MLflow server such as the one in the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md).

## 🔀 Tracking Backends

`TRACKING_BACKEND` selects where runs, metrics, and registered checkpoints go. Jobs default to `azureml`.

| Backend   | Runs and metrics                        | Checkpoint registry          | Required configuration                                                    |
|-----------|-----------------------------------------|------------------------------|---------------------------------------------------------------------------|
| `azureml` | Azure ML workspace MLflow endpoint      | Azure ML model registry      | `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZUREML_WORKSPACE_NAME` |
| `mlflow`  | MLflow server at `MLFLOW_TRACKING_URI`  | That server's model registry | `MLFLOW_TRACKING_URI`                                                     |
| `none`    | Not recorded; outputs stay with the job | None                         | None                                                                      |

Azure SDKs load only for `azureml`. The RL launchers also accept `--tracking-backend`, and `--disable-mlflow` selects `none`. With `none`, a checkpoint download (`--checkpoint-uri`) or registration request (`--register-checkpoint`, `REGISTER_CHECKPOINT`) fails before training starts.

## 📊 MLflow Tracking

Isaac Lab training with SKRL logs metrics automatically through monkey-patching.

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

LeRobot training logs to the selected backend on both OSMO and Azure ML; `none` trains without tracking. Submit an OSMO training job:

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

### MLflow Registry

With the `mlflow` backend, each registration adds a version to the named model. The version points at a directory of run artifacts:

- LeRobot checkpoints register the `checkpoints/<step>` directory the run already logged.
- Single-file RL checkpoints, and checkpoints the run could not log, are first logged into `checkpoints/registered/<name>`, because MLflow resolves a version's source as a directory. When the run already holds that name, the new directory gets a numeric suffix, such as `pretrained_model-2`, so a later checkpoint never replaces the files an earlier version points at.

Pass `models:/<name>/<version>` as a `--checkpoint-uri`, or download it with the MLflow CLI.

### Retrieve Registered Models

```bash
# Download from Azure ML
az ml model download \
  --name anymal-c-velocity --version 1 \
  --download-path ./checkpoint

# Download from a self-hosted MLflow server
MLFLOW_TRACKING_URI=http://<mlflow-host>:5000 \
  mlflow artifacts download --artifact-uri models:/anymal-c-velocity/1 --dst-path ./checkpoint

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
- [Local k3s Profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md) for the self-hosted MLflow server
- [Isaac Lab Training](isaac-lab-training.md) for RL training workflows
- [LeRobot Training](lerobot-training.md) for behavioral cloning workflows
- [Scripts Reference](../reference/scripts.md) for full CLI parameter tables

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
