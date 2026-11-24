---
sidebar_position: 3
title: Script Reference
description: Submission script inventory, CLI arguments, variable reference, and configuration for AzureML and OSMO training and inference pipelines.
author: Microsoft Robotics-AI Team
ms.date: 2026-10-15
ms.topic: reference
keywords:
  - scripts
  - cli
  - azureml
  - osmo
  - submission
  - variables
---

Inventory of submission scripts for training, evaluation, and inference workflows on Azure ML and OSMO platforms. Each entry includes CLI arguments, environment variable overrides, and platform profile and Terraform output resolution.

> [!NOTE]
> For detailed submission examples, see [Script Examples](scripts-examples.md).

## Submission Scripts

Run each script from the repository root.

| Script                                                         | Purpose                                                                                                     | Platform |
|----------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|----------|
| `training/rl/scripts/submit-azureml-training.sh`               | Package `training/rl/` and submit an Isaac Lab training job                                                 | Azure ML |
| `training/rl/scripts/submit-osmo-training.sh`                  | Stage `training/rl/` in object storage and submit an Isaac Lab training workflow                            | OSMO     |
| `training/rl/scripts/submit-osmo-dataset-training.sh`          | Submit an Isaac Lab training workflow with the code injected as a dataset folder                            | OSMO     |
| `training/il/scripts/submit-azureml-lerobot-training.sh`       | Submit LeRobot behavioral cloning training                                                                  | Azure ML |
| `training/il/scripts/submit-azureml-lerobot-pipeline.sh`       | Submit the LeRobot preprocess, train, evaluate, and register pipeline                                       | Azure ML |
| `training/il/scripts/submit-osmo-lerobot-training.sh`          | Submit LeRobot ACT, Diffusion, or SmolVLA training                                                          | OSMO     |
| `training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh`  | Submit GR00T VLA fine-tuning                                                                                | OSMO     |
| `training/smoke/scripts/submit-azureml-gpu-smoke.sh`           | Smoke-test a GPU target and training services                                                               | Azure ML |
| `training/pipelines/run-lerobot-pipeline.sh`                   | Train, evaluate, and register; its evaluation stage is not working ([Script Examples](scripts-examples.md)) | OSMO     |
| `evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh` | Submit Isaac Lab policy evaluation                                                                          | Azure ML |
| `evaluation/sil/scripts/submit-azureml-lerobot-eval.sh`        | Submit LeRobot policy evaluation                                                                            | Azure ML |
| `evaluation/sil/scripts/submit-azureml-vla-pi0-eval.sh`        | Submit pi0-family replay evaluation                                                                         | Azure ML |
| `evaluation/sil/scripts/submit-osmo-eval.sh`                   | Submit gated Isaac Lab policy evaluation                                                                    | OSMO     |
| `evaluation/sil/scripts/submit-osmo-lerobot-eval.sh`           | Submit LeRobot policy evaluation                                                                            | OSMO     |
| `evaluation/vlm_judge/scripts/submit-osmo-judge.sh`            | Score dataset episodes or policy rollout videos with a VLM judge                                            | OSMO     |
| `workflows/azureml/submit-osmo-proxy-job.sh`                   | Submit an Azure ML job that proxies an OSMO workflow                                                        | Azure ML |

## Quick Start

Scripts auto-detect Azure context from Terraform outputs in `infrastructure/terraform/`:

```bash
# Azure ML training
training/rl/scripts/submit-azureml-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# OSMO training
training/rl/scripts/submit-osmo-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# OSMO training (dataset folder upload)
training/rl/scripts/submit-osmo-dataset-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# LeRobot behavioral cloning (OSMO)
training/il/scripts/submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

# LeRobot behavioral cloning from Azure Blob (OSMO)
training/il/scripts/submit-osmo-lerobot-training.sh \
  --blob-url https://account.blob.core.windows.net/datasets/pusht

# LeRobot behavioral cloning (Azure ML)
training/il/scripts/submit-azureml-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

# LeRobot evaluation of a Hub policy, pinned to commits (OSMO)
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --policy-repo-id user/trained-policy \
  --policy-revision "<policy-commit-sha>" \
  --dataset-repo-id user/dataset \
  --dataset-revision "<dataset-commit-sha>"

# Isaac Lab evaluation of a registered model (Azure ML)
evaluation/sil/scripts/submit-azureml-isaaclab-evaluation.sh --model-name anymal-c-velocity --model-version 1

# Local OSMO profile: local dataset, in-cluster MLflow, no Azure
training/il/scripts/submit-osmo-lerobot-training.sh --profile local --local-dataset my-learner-dataset
```

## Prerequisites

Common requirements:

- Bash 4+
- Terraform outputs available in `infrastructure/terraform/` (or provide the same values via CLI / environment variables) for the `azure` platform profile
- A workstation deployed with the [local OSMO profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md) for the `local` platform profile

Script-specific tools:

- Azure ML scripts: `az` CLI + `az extension add --name ml`
- Validation: `jq`
- OSMO scripts: `osmo`
- OSMO code upload: `zip`
- Dataset injection submission: `rsync`

## CLI Arguments

Values resolve in order: **CLI arguments → environment variables → platform profile → Terraform outputs** (when applicable).

### Platform Profile Options

Every OSMO submission script accepts these options. The `azure` profile reads Terraform outputs and tracks to Azure ML; the `local` profile submits to the workstation's standalone OSMO and tracks to its MLflow server. A `local` submission passes Azure ML workspace values to the job only when `TRACKING_BACKEND=azureml`, which also needs the local profile's [optional Azure access](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/azure-access.md).

| Option             | Default                                       | Description                                                                                   | Source             |
|--------------------|-----------------------------------------------|-----------------------------------------------------------------------------------------------|--------------------|
| `--profile`        | `azure`                                       | Platform profile: `azure` or `local`                                                          | `PLATFORM_PROFILE` |
| `--cpu`            | profile task CPU, then the script default     | CPU cores per task                                                                            | `OSMO_CPU`         |
| `--memory`         | profile task memory, then the script default  | Memory per task, with unit                                                                    | `OSMO_MEMORY`      |
| `--storage`        | profile task storage, then the script default | Ephemeral storage per task, with unit                                                         | `OSMO_STORAGE`     |
| `--local-dataset`  | unset                                         | Dataset folder in the local datasets directory, for LeRobot training and evaluation and GR00T | CLI                |
| `--config-preview` | `false`                                       | Print the resolved configuration, including the profile, and exit                             | CLI                |
| n/a                | `azureml` (`azure`), `mlflow` (`local`)       | Tracking backend: `azureml`, `mlflow`, or `none`                                              | `TRACKING_BACKEND` |

`--use-local-osmo` selects the `osmo-dev` CLI build and is unrelated to the `local` profile. See [Local OSMO Profile](https://github.com/microsoft/physical-ai-toolchain/blob/main/infrastructure/local/README.md#-submitting-jobs) for what the `local` profile changes.

> [!NOTE]
> Isaac Lab image defaults come from `scripts/lib/common.sh` (`DEFAULT_ISAAC_LAB_IMAGE`; `DEFAULT_ISAAC_LAB_IMAGE_VERSION` is the derived tag-only value). OSMO workflow YAML image literals are direct-workflow fallbacks and should stay in sync with those shared defaults. AzureML environment versions default to a digest-aware value derived from `--image`.

### `submit-azureml-training.sh`

| Option                         | Default                                                                  | Description                                     | Source                                  |
|--------------------------------|--------------------------------------------------------------------------|-------------------------------------------------|-----------------------------------------|
| `--environment-name`           | `isaaclab-training-env`                                                  | AzureML environment name                        | CLI                                     |
| `--environment-version`        | derived from `--image` tag/digest                                        | AzureML environment version                     | CLI                                     |
| `--image` / `-i`               | `DEFAULT_ISAAC_LAB_IMAGE` (`nvcr.io/nvidia/isaac-lab:3.0.0-beta2-post1`) | Container image                                 | CLI                                     |
| `--assets-only`                | `false`                                                                  | Register environment without submitting a job   | CLI                                     |
| `--job-file` / `-w`            | `workflows/azureml/train.yaml`                                           | Job YAML template                               | CLI                                     |
| `--task` / `-t`                | `Isaac-Velocity-Rough-Anymal-C-v0`                                       | Isaac Lab task                                  | `TASK`                                  |
| `--num-envs` / `-n`            | `2048`                                                                   | Number of parallel environments                 | `NUM_ENVS`                              |
| `--max-iterations` / `-m`      | unset                                                                    | Max iterations (empty to unset)                 | `MAX_ITERATIONS`                        |
| `--checkpoint-uri` / `-c`      | unset                                                                    | MLflow checkpoint artifact URI                  | `CHECKPOINT_URI`                        |
| `--checkpoint-mode` / `-M`     | `from-scratch`                                                           | `from-scratch`, `warm-start`, `resume`, `fresh` | `CHECKPOINT_MODE`                       |
| `--register-checkpoint` / `-r` | derived from task                                                        | Model name for checkpoint registration          | `REGISTER_CHECKPOINT`                   |
| `--skip-register-checkpoint`   | `false`                                                                  | Skip automatic model registration               | CLI                                     |
| `--headless`                   | `true`                                                                   | Force headless rendering                        | CLI                                     |
| `--gui` / `--no-headless`      | `false`                                                                  | Disable headless mode                           | CLI                                     |
| `--run-smoke-test` / `-s`      | `false`                                                                  | Run Azure connectivity smoke test before submit | `RUN_AZURE_SMOKE_TEST`                  |
| `--mode`                       | `train`                                                                  | Execution mode                                  | CLI                                     |
| `--subscription-id`            | from TF                                                                  | Azure subscription ID                           | `AZURE_SUBSCRIPTION_ID` / TF            |
| `--resource-group`             | from TF                                                                  | Azure resource group                            | `AZURE_RESOURCE_GROUP` / TF             |
| `--workspace-name`             | from TF                                                                  | Azure ML workspace                              | `AZUREML_WORKSPACE_NAME` / TF           |
| `--compute`                    | from TF                                                                  | Compute target override                         | `AZUREML_COMPUTE` / TF                  |
| `--instance-type`              | `gpuspot`                                                                | Instance type                                   | CLI                                     |
| `--experiment-name`            | unset                                                                    | Experiment name override                        | CLI                                     |
| `--job-name`                   | unset                                                                    | Job name override                               | CLI                                     |
| `--display-name`               | unset                                                                    | Display name override                           | CLI                                     |
| `--stream`                     | `false`                                                                  | Stream logs after submission                    | CLI                                     |
| `--mlflow-token-retries`       | `3`                                                                      | MLflow token refresh retries                    | `MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES` |
| `--mlflow-http-timeout`        | `60`                                                                     | MLflow HTTP request timeout (seconds)           | `MLFLOW_HTTP_REQUEST_TIMEOUT`           |
| `--`                           | n/a                                                                      | Forward remaining args to `az ml job create`    | CLI                                     |

Example:

```bash
./submit-azureml-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --num-envs 1024 \
  --stream
```

### `submit-azureml-gpu-smoke.sh`

Located in `training/smoke/scripts/`. See [Smoke-Test a GPU Target](../training/azureml-training.md#-smoke-test-a-gpu-target) for the checks it runs.

| Option                  | Default                                                                         | Description                                                        | Source                            |
|-------------------------|---------------------------------------------------------------------------------|--------------------------------------------------------------------|-----------------------------------|
| `--instance-type`       | `gpuspot`                                                                       | InstanceType to test                                               | CLI                               |
| `--steps`               | `200`                                                                           | Training steps                                                     | CLI                               |
| `--checkpoint-interval` | `50`                                                                            | Steps between checkpoints                                          | CLI                               |
| `--model-name`          | `gpu-smoke-test`                                                                | Registered model name                                              | CLI                               |
| `--skip-register-model` | `false`                                                                         | Skip the model registry check                                      | CLI                               |
| `--storage-account`     | from TF                                                                         | Storage account for the upload check; empty skips it               | `AZURE_STORAGE_ACCOUNT_NAME` / TF |
| `--environment-name`    | `gpu-smoke-env`                                                                 | AzureML environment name                                           | CLI                               |
| `--environment-version` | derived from `--image` tag/digest                                               | AzureML environment version                                        | CLI                               |
| `--image`               | `DEFAULT_AZUREML_SMOKE_IMAGE` (`pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`) | Container image                                                    | CLI                               |
| `--job-file` / `-w`     | `training/smoke/workflows/azureml/gpu-smoke.yaml`                               | Job YAML template                                                  | CLI                               |
| `--subscription-id`     | from TF                                                                         | Azure subscription ID                                              | `AZURE_SUBSCRIPTION_ID` / TF      |
| `--resource-group`      | from TF                                                                         | Azure resource group                                               | `AZURE_RESOURCE_GROUP` / TF       |
| `--workspace-name`      | from TF                                                                         | Azure ML workspace                                                 | `AZUREML_WORKSPACE_NAME` / TF     |
| `--compute`             | from TF                                                                         | Compute target                                                     | `AZUREML_COMPUTE` / TF            |
| `--experiment-name`     | `gpu-smoke`                                                                     | Experiment name                                                    | CLI                               |
| `--job-name`            | unset                                                                           | Job name override                                                  | CLI                               |
| `--stream`              | `false`                                                                         | Stream logs, then verify the job status and its checkpoints output | CLI                               |
| `--config-preview`      | `false`                                                                         | Print configuration and exit                                       | CLI                               |
| `--`                    | n/a                                                                             | Forward remaining args to `az ml job create`                       | CLI                               |

Example:

```bash
./submit-azureml-gpu-smoke.sh --instance-type <instance-type> --stream
```

### `submit-azureml-isaaclab-evaluation.sh`

| Option                  | Default                                                                  | Description                                      | Source                        |
|-------------------------|--------------------------------------------------------------------------|--------------------------------------------------|-------------------------------|
| `--model-name`          | derived from task                                                        | Azure ML model name                              | CLI                           |
| `--model-version`       | `latest`                                                                 | Azure ML model version                           | CLI                           |
| `--environment-name`    | `isaaclab-training-env`                                                  | AzureML environment name                         | CLI                           |
| `--environment-version` | derived from `--image` tag/digest                                        | AzureML environment version                      | CLI                           |
| `--image`               | `DEFAULT_ISAAC_LAB_IMAGE` (`nvcr.io/nvidia/isaac-lab:3.0.0-beta2-post1`) | Container image                                  | CLI                           |
| `--task`                | `Isaac-Velocity-Rough-Anymal-C-v0`                                       | Override task ID                                 | `TASK`                        |
| `--framework`           | unset                                                                    | Override framework                               | CLI                           |
| `--eval-episodes`       | `100`                                                                    | Evaluation episodes                              | CLI                           |
| `--num-envs`            | `64`                                                                     | Parallel environments                            | CLI                           |
| `--success-threshold`   | unset                                                                    | Success threshold (defaults from model metadata) | CLI                           |
| `--headless`            | `true`                                                                   | Run headless                                     | CLI                           |
| `--gui`                 | `false`                                                                  | Disable headless mode                            | CLI                           |
| `--job-file`            | `workflows/azureml/isaaclab-evaluation.yaml`                             | Job YAML template                                | CLI                           |
| `--compute`             | from TF                                                                  | Compute target override                          | `AZUREML_COMPUTE` / TF        |
| `--instance-type`       | `gpuspot`                                                                | Instance type                                    | CLI                           |
| `--experiment-name`     | unset                                                                    | Experiment name override                         | CLI                           |
| `--job-name`            | unset                                                                    | Job name override                                | CLI                           |
| `--stream`              | `false`                                                                  | Stream logs after submission                     | CLI                           |
| `--subscription-id`     | from TF                                                                  | Azure subscription ID                            | `AZURE_SUBSCRIPTION_ID` / TF  |
| `--resource-group`      | from TF                                                                  | Azure resource group                             | `AZURE_RESOURCE_GROUP` / TF   |
| `--workspace-name`      | from TF                                                                  | Azure ML workspace                               | `AZUREML_WORKSPACE_NAME` / TF |

Example:

```bash
./submit-azureml-isaaclab-evaluation.sh \
  --model-name anymal-c-velocity \
  --model-version 1 \
  --stream
```

### `submit-osmo-training.sh`

| Option                         | Default                                                                  | Description                                      | Source                        |
|--------------------------------|--------------------------------------------------------------------------|--------------------------------------------------|-------------------------------|
| `--profile`                    | `azure`                                                                  | Platform profile: `azure` or `local`             | `PLATFORM_PROFILE`            |
| `--workflow` / `-w`            | `workflows/osmo/train.yaml`                                              | Workflow template                                | CLI                           |
| `--task` / `-t`                | `Isaac-Velocity-Rough-Anymal-C-v0`                                       | Isaac Lab task                                   | `TASK`                        |
| `--num-envs` / `-n`            | `2048`                                                                   | Number of parallel environments                  | `NUM_ENVS`                    |
| `--max-iterations` / `-m`      | unset                                                                    | Max iterations (empty to unset)                  | `MAX_ITERATIONS`              |
| `--image` / `-i`               | `DEFAULT_ISAAC_LAB_IMAGE` (`nvcr.io/nvidia/isaac-lab:3.0.0-beta2-post1`) | Container image                                  | `IMAGE`                       |
| `--payload-root` / `-p`        | `/tmp/isaac_payload`                                                     | Runtime extraction root                          | `PAYLOAD_ROOT`                |
| `--backend` / `-b`             | `skrl`                                                                   | Training backend: `skrl` (default), `rsl_rl`     | `TRAINING_BACKEND`            |
| `--gpu`, `--cpu`               | profile, then `1`, `30`                                                  | GPUs and CPU cores per task                      | `OSMO_GPU`, `OSMO_CPU`        |
| `--memory`, `--storage`        | profile, then `400Gi`, `200Gi`                                           | Memory and storage per task                      | `OSMO_MEMORY`, `OSMO_STORAGE` |
| `--checkpoint-uri` / `-c`      | unset                                                                    | MLflow checkpoint artifact URI                   | `CHECKPOINT_URI`              |
| `--checkpoint-mode` / `-M`     | `from-scratch`                                                           | `from-scratch`, `warm-start`, `resume`, `fresh`  | `CHECKPOINT_MODE`             |
| `--register-checkpoint` / `-r` | derived from task                                                        | Model name for checkpoint registration           | `REGISTER_CHECKPOINT`         |
| `--skip-register-checkpoint`   | `false`                                                                  | Skip automatic model registration                | CLI                           |
| `--sleep-after-unpack`         | unset                                                                    | Sleep seconds post-unpack (debug)                | `SLEEP_AFTER_UNPACK`          |
| `--run-smoke-test` / `-s`      | `false`                                                                  | Enable Azure connectivity smoke test             | `RUN_AZURE_SMOKE_TEST`        |
| `--azure-subscription-id`      | from TF                                                                  | Azure subscription ID                            | `AZURE_SUBSCRIPTION_ID` / TF  |
| `--azure-resource-group`       | from TF                                                                  | Azure resource group                             | `AZURE_RESOURCE_GROUP` / TF   |
| `--azure-workspace-name`       | from TF                                                                  | Azure ML workspace                               | `AZUREML_WORKSPACE_NAME` / TF |
| `--`                           | n/a                                                                      | Forward remaining args to `osmo workflow submit` | CLI                           |

Example:

```bash
./submit-osmo-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --backend skrl \
  -- --dry-run
```

### `submit-osmo-dataset-training.sh` (dataset injection)

| Option                         | Default                                                                  | Description                                      | Source                        |
|--------------------------------|--------------------------------------------------------------------------|--------------------------------------------------|-------------------------------|
| `--profile`                    | `azure`                                                                  | Platform profile: `azure` or `local`             | `PLATFORM_PROFILE`            |
| `--workflow` / `-w`            | `workflows/osmo/train-dataset.yaml`                                      | Workflow template                                | CLI                           |
| `--task` / `-t`                | `Isaac-Velocity-Rough-Anymal-C-v0`                                       | Isaac Lab task                                   | `TASK`                        |
| `--num-envs` / `-n`            | `2048`                                                                   | Number of parallel environments                  | `NUM_ENVS`                    |
| `--max-iterations` / `-m`      | unset                                                                    | Max iterations (empty to unset)                  | `MAX_ITERATIONS`              |
| `--image` / `-i`               | `DEFAULT_ISAAC_LAB_IMAGE` (`nvcr.io/nvidia/isaac-lab:3.0.0-beta2-post1`) | Container image                                  | `IMAGE`                       |
| `--backend` / `-b`             | `skrl`                                                                   | Training backend: `skrl` (default), `rsl_rl`     | `TRAINING_BACKEND`            |
| `--gpu`, `--cpu`               | profile, then `1`, `30`                                                  | GPUs and CPU cores per task                      | `OSMO_GPU`, `OSMO_CPU`        |
| `--memory`, `--storage`        | profile, then `400Gi`, `200Gi`                                           | Memory and storage per task                      | `OSMO_MEMORY`, `OSMO_STORAGE` |
| `--dataset-bucket`             | `training`                                                               | OSMO bucket name                                 | `OSMO_DATASET_BUCKET`         |
| `--dataset-name`               | `training-code`                                                          | Dataset name (auto-versioned)                    | `OSMO_DATASET_NAME`           |
| `--training-path`              | `training/`                                                              | Local path to upload                             | `TRAINING_PATH`               |
| `--checkpoint-uri` / `-c`      | unset                                                                    | MLflow checkpoint artifact URI                   | `CHECKPOINT_URI`              |
| `--checkpoint-mode` / `-M`     | `from-scratch`                                                           | `from-scratch`, `warm-start`, `resume`, `fresh`  | `CHECKPOINT_MODE`             |
| `--register-checkpoint` / `-r` | derived from task                                                        | Model name for checkpoint registration           | `REGISTER_CHECKPOINT`         |
| `--skip-register-checkpoint`   | `false`                                                                  | Skip automatic model registration                | CLI                           |
| `--run-smoke-test` / `-s`      | `false`                                                                  | Enable Azure connectivity smoke test             | `RUN_AZURE_SMOKE_TEST`        |
| `--azure-subscription-id`      | from TF                                                                  | Azure subscription ID                            | `AZURE_SUBSCRIPTION_ID` / TF  |
| `--azure-resource-group`       | from TF                                                                  | Azure resource group                             | `AZURE_RESOURCE_GROUP` / TF   |
| `--azure-workspace-name`       | from TF                                                                  | Azure ML workspace                               | `AZUREML_WORKSPACE_NAME` / TF |
| `--`                           | n/a                                                                      | Forward remaining args to `osmo workflow submit` | CLI                           |

Example:

```bash
./submit-osmo-dataset-training.sh \
  --task Isaac-Velocity-Rough-Anymal-C-v0 \
  --dataset-name my-training-v1
```

## Configuration

Scripts resolve values in order: CLI arguments → environment variables → platform profile → Terraform outputs.

| Variable                 | Description                                                   |
|--------------------------|---------------------------------------------------------------|
| `PLATFORM_PROFILE`       | Platform profile: `azure` or `local`                          |
| `TRACKING_BACKEND`       | Tracking backend: `azureml`, `mlflow`, or `none`              |
| `MLFLOW_TRACKING_URI`    | MLflow server for the `mlflow` backend on the `azure` profile |
| `AZURE_SUBSCRIPTION_ID`  | Azure subscription                                            |
| `AZURE_RESOURCE_GROUP`   | Resource group name                                           |
| `AZUREML_WORKSPACE_NAME` | ML workspace name                                             |
| `TASK`                   | Isaac Lab task name                                           |
| `NUM_ENVS`               | Number of parallel environments                               |
| `OSMO_DATASET_BUCKET`    | Dataset bucket for OSMO training                              |
| `OSMO_DATASET_NAME`      | Dataset name for OSMO training                                |
| `DATASET_REPO_ID`        | HuggingFace dataset repo ID                                   |
| `POLICY_TYPE`            | LeRobot policy architecture                                   |

## Script Library

| File                               | Purpose                                                             |
|------------------------------------|---------------------------------------------------------------------|
| `scripts/lib/terraform-outputs.sh` | Shared functions for reading Terraform outputs                      |
| `scripts/lib/platform-profile.sh`  | Platform profile resolver and helpers shared by OSMO submit scripts |

Source the library to use helper functions:

```bash
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
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
