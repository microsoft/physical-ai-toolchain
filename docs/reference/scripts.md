---
sidebar_position: 3
title: Script Reference
description: Submission script inventory, CLI arguments, variable reference, and configuration for AzureML and OSMO training and inference pipelines.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-08
ms.topic: reference
keywords:
  - scripts
  - cli
  - azureml
  - osmo
  - submission
  - variables
---

Inventory of submission scripts for training, validation, and inference workflows on Azure ML and OSMO platforms. Each entry includes CLI arguments, environment variable overrides, and Terraform output resolution.

> [!NOTE]
> For detailed submission examples, see [Script Examples](scripts-examples.md).

## Submission Scripts

| Script                               | Purpose                                             | Platform |
|--------------------------------------|-----------------------------------------------------|----------|
| `submit-azureml-training.sh`         | Package code and submit Azure ML training job       | Azure ML |
| `submit-azureml-validation.sh`       | Submit model validation job                         | Azure ML |
| `submit-azureml-lerobot-training.sh` | Submit LeRobot training to Azure ML                 | Azure ML |
| `submit-osmo-training.sh`            | Package code and submit OSMO workflow (base64)      | OSMO     |
| `submit-osmo-dataset-training.sh`    | Submit OSMO workflow using dataset folder injection | OSMO     |
| `submit-osmo-lerobot-training.sh`    | Submit LeRobot behavioral cloning training          | OSMO     |
| `submit-osmo-lerobot-inference.sh`   | Submit LeRobot inference/evaluation                 | OSMO     |
| `run-lerobot-pipeline.sh`            | End-to-end train → evaluate → register pipeline     | OSMO     |

## Quick Start

Scripts auto-detect Azure context from Terraform outputs in `infrastructure/terraform/`:

```bash
# Azure ML training
./submit-azureml-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# OSMO training (base64 encoded)
./submit-osmo-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# OSMO training (dataset folder upload)
./submit-osmo-dataset-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# LeRobot behavioral cloning (OSMO)
./submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

# LeRobot behavioral cloning (Azure ML)
./submit-azureml-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

# LeRobot inference/evaluation
./submit-osmo-lerobot-inference.sh --policy-repo-id user/trained-policy

# End-to-end pipeline: train → evaluate → register
./run-lerobot-pipeline.sh \
  -d lerobot/aloha_sim_insertion_human \
  --policy-repo-id user/my-policy \
  -r my-model

# Validation (requires registered model)
./submit-azureml-validation.sh --model-name anymal-c-velocity --model-version 1
```

## Prerequisites

Common requirements:

- Bash 4+
- Terraform outputs available in `infrastructure/terraform/` (or provide the same values via CLI / environment variables)

Script-specific tools:

- Azure ML scripts: `az` CLI + `az extension add --name ml`
- Validation: `jq`
- OSMO scripts: `osmo`
- Base64 payload submission: `zip`, `base64`
- Dataset injection submission: `rsync`

## CLI Arguments

Values resolve in order: **CLI arguments → environment variables → Terraform outputs** (when applicable).

### `submit-azureml-training.sh`

| Option                         | Default                            | Description                                     | Source                                  |
|--------------------------------|------------------------------------|-------------------------------------------------|-----------------------------------------|
| `--environment-name`           | `isaaclab-training-env`            | AzureML environment name                        | CLI                                     |
| `--environment-version`        | `2.3.2`                            | AzureML environment version                     | CLI                                     |
| `--image` / `-i`               | `nvcr.io/nvidia/isaac-lab:2.3.2`   | Container image                                 | CLI                                     |
| `--assets-only`                | `false`                            | Register environment without submitting a job   | CLI                                     |
| `--job-file` / `-w`            | `workflows/azureml/train.yaml`     | Job YAML template                               | CLI                                     |
| `--task` / `-t`                | `Isaac-Velocity-Rough-Anymal-C-v0` | IsaacLab task                                   | `TASK`                                  |
| `--num-envs` / `-n`            | `2048`                             | Number of parallel environments                 | `NUM_ENVS`                              |
| `--max-iterations` / `-m`      | unset                              | Max iterations (empty to unset)                 | `MAX_ITERATIONS`                        |
| `--checkpoint-uri` / `-c`      | unset                              | MLflow checkpoint artifact URI                  | `CHECKPOINT_URI`                        |
| `--checkpoint-mode` / `-M`     | `from-scratch`                     | `from-scratch`, `warm-start`, `resume`, `fresh` | `CHECKPOINT_MODE`                       |
| `--register-checkpoint` / `-r` | derived from task                  | Model name for checkpoint registration          | `REGISTER_CHECKPOINT`                   |
| `--skip-register-checkpoint`   | `false`                            | Skip automatic model registration               | CLI                                     |
| `--headless`                   | `true`                             | Force headless rendering                        | CLI                                     |
| `--gui` / `--no-headless`      | `false`                            | Disable headless mode                           | CLI                                     |
| `--run-smoke-test` / `-s`      | `false`                            | Run Azure connectivity smoke test before submit | `RUN_AZURE_SMOKE_TEST`                  |
| `--mode`                       | `train`                            | Execution mode                                  | CLI                                     |
| `--subscription-id`            | from TF                            | Azure subscription ID                           | `AZURE_SUBSCRIPTION_ID` / TF            |
| `--resource-group`             | from TF                            | Azure resource group                            | `AZURE_RESOURCE_GROUP` / TF             |
| `--workspace-name`             | from TF                            | Azure ML workspace                              | `AZUREML_WORKSPACE_NAME` / TF           |
| `--compute`                    | from TF                            | Compute target override                         | `AZUREML_COMPUTE` / TF                  |
| `--instance-type`              | `gpuspot`                          | Instance type                                   | CLI                                     |
| `--experiment-name`            | unset                              | Experiment name override                        | CLI                                     |
| `--job-name`                   | unset                              | Job name override                               | CLI                                     |
| `--display-name`               | unset                              | Display name override                           | CLI                                     |
| `--stream`                     | `false`                            | Stream logs after submission                    | CLI                                     |
| `--mlflow-token-retries`       | `3`                                | MLflow token refresh retries                    | `MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES` |
| `--mlflow-http-timeout`        | `60`                               | MLflow HTTP request timeout (seconds)           | `MLFLOW_HTTP_REQUEST_TIMEOUT`           |
| `--`                           | n/a                                | Forward remaining args to `az ml job create`    | CLI                                     |

Example:

```bash
./submit-azureml-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --num-envs 1024 \
  --stream
```

### `submit-azureml-validation.sh`

| Option                  | Default                            | Description                                      | Source                        |
|-------------------------|------------------------------------|--------------------------------------------------|-------------------------------|
| `--model-name`          | derived from task                  | Azure ML model name                              | CLI                           |
| `--model-version`       | `latest`                           | Azure ML model version                           | CLI                           |
| `--environment-name`    | `isaaclab-training-env`            | AzureML environment name                         | CLI                           |
| `--environment-version` | `2.3.2`                            | AzureML environment version                      | CLI                           |
| `--image`               | `nvcr.io/nvidia/isaac-lab:2.3.2`   | Container image                                  | CLI                           |
| `--task`                | `Isaac-Velocity-Rough-Anymal-C-v0` | Override task ID                                 | `TASK`                        |
| `--framework`           | unset                              | Override framework                               | CLI                           |
| `--eval-episodes`       | `100`                              | Evaluation episodes                              | CLI                           |
| `--num-envs`            | `64`                               | Parallel environments                            | CLI                           |
| `--success-threshold`   | unset                              | Success threshold (defaults from model metadata) | CLI                           |
| `--headless`            | `true`                             | Run headless                                     | CLI                           |
| `--gui`                 | `false`                            | Disable headless mode                            | CLI                           |
| `--job-file`            | `workflows/azureml/validate.yaml`  | Job YAML template                                | CLI                           |
| `--compute`             | from TF                            | Compute target override                          | `AZUREML_COMPUTE` / TF        |
| `--instance-type`       | `gpuspot`                          | Instance type                                    | CLI                           |
| `--experiment-name`     | unset                              | Experiment name override                         | CLI                           |
| `--job-name`            | unset                              | Job name override                                | CLI                           |
| `--stream`              | `false`                            | Stream logs after submission                     | CLI                           |
| `--subscription-id`     | from TF                            | Azure subscription ID                            | `AZURE_SUBSCRIPTION_ID` / TF  |
| `--resource-group`      | from TF                            | Azure resource group                             | `AZURE_RESOURCE_GROUP` / TF   |
| `--workspace-name`      | from TF                            | Azure ML workspace                               | `AZUREML_WORKSPACE_NAME` / TF |

Example:

```bash
./submit-azureml-validation.sh \
  --model-name anymal-c-velocity \
  --model-version 1 \
  --stream
```

### `submit-osmo-training.sh` (base64 payload)

| Option                         | Default                            | Description                                      | Source                        |
|--------------------------------|------------------------------------|--------------------------------------------------|-------------------------------|
| `--workflow` / `-w`            | `workflows/osmo/train.yaml`        | Workflow template                                | CLI                           |
| `--task` / `-t`                | `Isaac-Velocity-Rough-Anymal-C-v0` | IsaacLab task                                    | `TASK`                        |
| `--num-envs` / `-n`            | `2048`                             | Number of parallel environments                  | `NUM_ENVS`                    |
| `--max-iterations` / `-m`      | unset                              | Max iterations (empty to unset)                  | `MAX_ITERATIONS`              |
| `--image` / `-i`               | `nvcr.io/nvidia/isaac-lab:2.3.2`   | Container image                                  | `IMAGE`                       |
| `--payload-root` / `-p`        | `/workspace/isaac_payload`         | Runtime extraction root                          | `PAYLOAD_ROOT`                |
| `--backend` / `-b`             | `skrl`                             | Training backend: `skrl` (default), `rsl_rl`     | `TRAINING_BACKEND`            |
| `--checkpoint-uri` / `-c`      | unset                              | MLflow checkpoint artifact URI                   | `CHECKPOINT_URI`              |
| `--checkpoint-mode` / `-M`     | `from-scratch`                     | `from-scratch`, `warm-start`, `resume`, `fresh`  | `CHECKPOINT_MODE`             |
| `--register-checkpoint` / `-r` | derived from task                  | Model name for checkpoint registration           | `REGISTER_CHECKPOINT`         |
| `--skip-register-checkpoint`   | `false`                            | Skip automatic model registration                | CLI                           |
| `--sleep-after-unpack`         | unset                              | Sleep seconds post-unpack (debug)                | `SLEEP_AFTER_UNPACK`          |
| `--run-smoke-test` / `-s`      | `false`                            | Enable Azure connectivity smoke test             | `RUN_AZURE_SMOKE_TEST`        |
| `--azure-subscription-id`      | from TF                            | Azure subscription ID                            | `AZURE_SUBSCRIPTION_ID` / TF  |
| `--azure-resource-group`       | from TF                            | Azure resource group                             | `AZURE_RESOURCE_GROUP` / TF   |
| `--azure-workspace-name`       | from TF                            | Azure ML workspace                               | `AZUREML_WORKSPACE_NAME` / TF |
| `--`                           | n/a                                | Forward remaining args to `osmo workflow submit` | CLI                           |

Example:

```bash
./submit-osmo-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --backend skrl \
  -- --dry-run
```

### `submit-osmo-dataset-training.sh` (dataset injection)

| Option                         | Default                             | Description                                      | Source                        |
|--------------------------------|-------------------------------------|--------------------------------------------------|-------------------------------|
| `--workflow` / `-w`            | `workflows/osmo/train-dataset.yaml` | Workflow template                                | CLI                           |
| `--task` / `-t`                | `Isaac-Velocity-Rough-Anymal-C-v0`  | IsaacLab task                                    | `TASK`                        |
| `--num-envs` / `-n`            | `2048`                              | Number of parallel environments                  | `NUM_ENVS`                    |
| `--max-iterations` / `-m`      | unset                               | Max iterations (empty to unset)                  | `MAX_ITERATIONS`              |
| `--image` / `-i`               | `nvcr.io/nvidia/isaac-lab:2.3.2`    | Container image                                  | `IMAGE`                       |
| `--backend` / `-b`             | `skrl`                              | Training backend: `skrl` (default), `rsl_rl`     | `TRAINING_BACKEND`            |
| `--dataset-bucket`             | `training`                          | OSMO bucket name                                 | `OSMO_DATASET_BUCKET`         |
| `--dataset-name`               | `training-code`                     | Dataset name (auto-versioned)                    | `OSMO_DATASET_NAME`           |
| `--training-path`              | `training/`                         | Local path to upload                             | `TRAINING_PATH`               |
| `--checkpoint-uri` / `-c`      | unset                               | MLflow checkpoint artifact URI                   | `CHECKPOINT_URI`              |
| `--checkpoint-mode` / `-M`     | `from-scratch`                      | `from-scratch`, `warm-start`, `resume`, `fresh`  | `CHECKPOINT_MODE`             |
| `--register-checkpoint` / `-r` | derived from task                   | Model name for checkpoint registration           | `REGISTER_CHECKPOINT`         |
| `--skip-register-checkpoint`   | `false`                             | Skip automatic model registration                | CLI                           |
| `--run-smoke-test` / `-s`      | `false`                             | Enable Azure connectivity smoke test             | `RUN_AZURE_SMOKE_TEST`        |
| `--azure-subscription-id`      | from TF                             | Azure subscription ID                            | `AZURE_SUBSCRIPTION_ID` / TF  |
| `--azure-resource-group`       | from TF                             | Azure resource group                             | `AZURE_RESOURCE_GROUP` / TF   |
| `--azure-workspace-name`       | from TF                             | Azure ML workspace                               | `AZUREML_WORKSPACE_NAME` / TF |
| `--`                           | n/a                                 | Forward remaining args to `osmo workflow submit` | CLI                           |

Example:

```bash
./submit-osmo-dataset-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --dataset-name my-training-v1
```

## Configuration

Scripts resolve values in order: CLI arguments → environment variables → Terraform outputs.

| Variable                 | Description                      |
|--------------------------|----------------------------------|
| `AZURE_SUBSCRIPTION_ID`  | Azure subscription               |
| `AZURE_RESOURCE_GROUP`   | Resource group name              |
| `AZUREML_WORKSPACE_NAME` | ML workspace name                |
| `TASK`                   | IsaacLab task name               |
| `NUM_ENVS`               | Number of parallel environments  |
| `OSMO_DATASET_BUCKET`    | Dataset bucket for OSMO training |
| `OSMO_DATASET_NAME`      | Dataset name for OSMO training   |
| `DATASET_REPO_ID`        | HuggingFace dataset repo ID      |
| `POLICY_TYPE`            | LeRobot policy architecture      |

## Script Library

| File                            | Purpose                                        |
|---------------------------------|------------------------------------------------|
| `shared/lib/terraform-outputs.sh` | Shared functions for reading Terraform outputs |

Source the library to use helper functions:

```bash
source "$REPO_ROOT/shared/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform"
get_aks_cluster_name   # Returns AKS cluster name
get_azureml_workspace  # Returns ML workspace name
```

## Related Documentation

- [Script Examples](scripts-examples.md) for detailed submission examples
- [Reference Hub](README.md) for all reference documentation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
