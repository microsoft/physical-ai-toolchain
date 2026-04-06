---
title: Workflow Templates (AzureML)
description: Canonical AzureML workflow template reference for training and evaluation jobs.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-01
ms.topic: reference
keywords:
  - azureml
  - workflows
  - templates
  - training
  - evaluation
---

Canonical AzureML workflow templates for RL and LeRobot training and evaluation.
Template names, defaults, and paths in this page are derived from the YAML files
in `training/` and `evaluation/`.

## Template Inventory

| Template             | Purpose                                                   | Source YAML path                                     | Typical submit path                                      |
|----------------------|-----------------------------------------------------------|------------------------------------------------------|----------------------------------------------------------|
| `train.yaml`         | IsaacLab RL training job structure                        | `training/rl/workflows/azureml/train.yaml`           | `training/rl/scripts/submit-azureml-training.sh`         |
| `lerobot-train.yaml` | LeRobot behavioral cloning training job structure         | `training/il/workflows/azureml/lerobot-train.yaml`   | `training/il/scripts/submit-azureml-lerobot-training.sh` |
| `validate.yaml`      | IsaacLab policy validation against registered models      | `evaluation/sil/workflows/azureml/validate.yaml`     | `evaluation/sil/scripts/submit-azureml-validation.sh`    |
| `lerobot-eval.yaml`  | LeRobot policy evaluation and optional model registration | `evaluation/sil/workflows/azureml/lerobot-eval.yaml` | `evaluation/sil/scripts/submit-azureml-lerobot-eval.sh`  |

## train.yaml

| Field                            | Details                                                                                                                                                                                                                                                                             |
|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Purpose                          | Structural template for IsaacLab RL training submissions in AzureML.                                                                                                                                                                                                                |
| Source YAML path                 | `training/rl/workflows/azureml/train.yaml`                                                                                                                                                                                                                                          |
| Primary parameters and overrides | `inputs.task` (`Isaac-Velocity-Rough-Anymal-C-v0`), `inputs.num_envs` (`"2048"`), `inputs.max_iterations` (`"600"`), `inputs.checkpoint_mode` (`from-scratch`), `inputs.checkpoint_uri` (`none`), `inputs.register_checkpoint` (`none`), `inputs.run_azure_smoke_test` (`"false"`). |
| Typical submit path              | `training/rl/scripts/submit-azureml-training.sh`                                                                                                                                                                                                                                    |
| Usage notes                      | Keep template values as structural defaults. The submit script sets runtime command, compute, and Azure context.                                                                                                                                                                    |

## lerobot-train.yaml

| Field                            | Details                                                                                                                                                                                                                                                                                                                           |
|----------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Purpose                          | Structural template for LeRobot ACT or Diffusion training on AzureML.                                                                                                                                                                                                                                                             |
| Source YAML path                 | `training/il/workflows/azureml/lerobot-train.yaml`                                                                                                                                                                                                                                                                                |
| Primary parameters and overrides | `inputs.dataset_repo_id` (`none`), `inputs.policy_type` (`act`), `inputs.job_name` (`lerobot-act-training`), `inputs.output_dir` (`/workspace/outputs/train`), `inputs.training_steps` (`none`), `inputs.batch_size` (`none`), `inputs.eval_freq` (`none`), `inputs.save_freq` (`"5000"`), `inputs.register_checkpoint` (`none`). |
| Typical submit path              | `training/il/scripts/submit-azureml-lerobot-training.sh`                                                                                                                                                                                                                                                                          |
| Usage notes                      | Use script flags for policy source and hyperparameters. Secrets such as HuggingFace and WANDB values are injected at submission time.                                                                                                                                                                                             |

## validate.yaml

| Field                            | Details                                                                                                                                                                                                    |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Purpose                          | Structural template for IsaacLab validation jobs against registered models.                                                                                                                                |
| Source YAML path                 | `evaluation/sil/workflows/azureml/validate.yaml`                                                                                                                                                           |
| Primary parameters and overrides | `inputs.trained_model.path` (`azureml:placeholder:1`), `inputs.task` (`auto`), `inputs.framework` (`auto`), `inputs.eval_episodes` (`100`), `inputs.num_envs` (`64`), `inputs.success_threshold` (`-1.0`). |
| Typical submit path              | `evaluation/sil/scripts/submit-azureml-validation.sh`                                                                                                                                                      |
| Usage notes                      | The script resolves model metadata and passes overrides with `--set`. The template intentionally uses sentinel defaults (`auto`, placeholder paths).                                                       |

## lerobot-eval.yaml

| Field                            | Details                                                                                                                                                                                                                                                                                                                       |
|----------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Purpose                          | Structural template for LeRobot evaluation and optional model registration on AzureML.                                                                                                                                                                                                                                        |
| Source YAML path                 | `evaluation/sil/workflows/azureml/lerobot-eval.yaml`                                                                                                                                                                                                                                                                          |
| Primary parameters and overrides | `inputs.policy_repo_id` (`none`), `inputs.policy_type` (`act`), `inputs.dataset_repo_id` (`none`), `inputs.eval_episodes` (`"10"`), `inputs.eval_batch_size` (`"10"`), `inputs.record_video` (`"false"`), `inputs.mlflow_enable` (`"false"`), `inputs.register_model` (`none`), `inputs.blob_storage_container` (`datasets`). |
| Typical submit path              | `evaluation/sil/scripts/submit-azureml-lerobot-eval.sh`                                                                                                                                                                                                                                                                       |
| Usage notes                      | This template is the canonical AzureML LeRobot evaluation reference.                                                                                                                                                                                                                                                          |

## Usage Notes

| Topic             | Guidance                                                                                                        |
|-------------------|-----------------------------------------------------------------------------------------------------------------|
| Source of truth   | Use YAML files in `training/` and `evaluation/` for template names, keys, and defaults.                         |
| Override pattern  | Treat templates as structure-first; submission scripts provide runtime command and environment-specific values. |
| Azure context     | Set `subscription_id`, `resource_group`, and `workspace_name` through script options or environment variables.  |
| Related reference | See [Reference index](README.md) for adjacent script and artifact guides.                                       |
