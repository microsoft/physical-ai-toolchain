---
title: Workflow Templates (OSMO)
description: Canonical OSMO workflow template reference for training and evaluation jobs.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-01
ms.topic: reference
keywords:
  - osmo
  - workflows
  - templates
  - training
  - evaluation
---

Canonical OSMO workflow templates for RL and LeRobot training and evaluation.
Template names in this page are based on current YAML files and exclude stale
legacy naming.

## Template Inventory

| Template | Purpose | Source YAML path | Typical submit path |
| --- | --- | --- | --- |
| `train.yaml` | IsaacLab RL training with inline payload archive | `training/rl/workflows/osmo/train.yaml` | `training/rl/scripts/submit-osmo-training.sh` |
| `train-dataset.yaml` | IsaacLab RL training with dataset folder injection | `training/il/workflows/osmo/train-dataset.yaml` | `training/il/scripts/submit-osmo-dataset-training.sh` |
| `lerobot-train.yaml` | LeRobot ACT or Diffusion training workflow | `training/il/workflows/osmo/lerobot-train.yaml` | `training/il/scripts/submit-osmo-lerobot-training.sh` |
| `eval.yaml` | IsaacLab checkpoint evaluation workflow | `evaluation/sil/workflows/osmo/eval.yaml` | `evaluation/sil/scripts/submit-osmo-eval.sh` |
| `lerobot-eval.yaml` | LeRobot policy evaluation workflow | `evaluation/sil/workflows/osmo/lerobot-eval.yaml` | `evaluation/sil/scripts/submit-osmo-lerobot-eval.sh` |

## train.yaml

| Field | Details |
| --- | --- |
| Purpose | OSMO RL training using a base64-encoded runtime payload. |
| Source YAML path | `training/rl/workflows/osmo/train.yaml` |
| Primary parameters and overrides | `default-values.task` (`Isaac-Velocity-Rough-Anymal-C-v0`), `default-values.num_envs` (`"2048"`), `default-values.max_iterations` (empty), `default-values.checkpoint_mode` (`from-scratch`), `default-values.training_backend` (`skrl`), `default-values.gpu` (`"1"`), `default-values.cpu` (`"30"`). |
| Typical submit path | `training/rl/scripts/submit-osmo-training.sh` |
| Usage notes | Use for RL training when shipping the runtime payload inline. Script flags typically override task, resources, and checkpoint behavior. |

## train-dataset.yaml

| Field | Details |
| --- | --- |
| Purpose | OSMO RL training that mounts training code from an uploaded dataset path. |
| Source YAML path | `training/il/workflows/osmo/train-dataset.yaml` |
| Primary parameters and overrides | `default-values.dataset_bucket` (`training`), `default-values.dataset_name` (`training-code`), `default-values.task` (`Isaac-Velocity-Rough-Anymal-C-v0`), `default-values.num_envs` (`"2048"`), `default-values.checkpoint_mode` (`from-scratch`), `default-values.training_backend` (`skrl`). |
| Typical submit path | `training/il/scripts/submit-osmo-dataset-training.sh` |
| Usage notes | Use when payload size or reuse favors dataset-based delivery. The script stages and uploads training sources before submission. |

## lerobot-train.yaml

| Field | Details |
| --- | --- |
| Purpose | OSMO LeRobot training with optional Azure Blob dataset source and checkpoint registration. |
| Source YAML path | `training/il/workflows/osmo/lerobot-train.yaml` |
| Primary parameters and overrides | `default-values.policy_type` (`act`), `default-values.dataset_repo_id` (empty), `default-values.training_steps` (`"100000"`), `default-values.batch_size` (`"32"`), `default-values.learning_rate` (`"1e-4"`), `default-values.save_freq` (`"5000"`), `default-values.storage_container` (`datasets`), `default-values.register_checkpoint` (empty). |
| Typical submit path | `training/il/scripts/submit-osmo-lerobot-training.sh` |
| Usage notes | Supports HuggingFace and blob-backed datasets. Keep policy type and data source aligned with script flags to avoid mixed-source configuration. |

## eval.yaml

| Field | Details |
| --- | --- |
| Purpose | OSMO IsaacLab checkpoint evaluation for policy export and validation. |
| Source YAML path | `evaluation/sil/workflows/osmo/eval.yaml` |
| Primary parameters and overrides | `default-values.task` (`Isaac-Ant-v0`), `default-values.num_envs` (`"4"`), `default-values.max_steps` (`"500"`), `default-values.video_length` (`"200"`), `default-values.checkpoint_uri` (empty), `default-values.inference_format` (`both`). |
| Typical submit path | `evaluation/sil/scripts/submit-osmo-eval.sh` |
| Usage notes | Requires checkpoint URI at submission. Use `inference_format` to control ONNX/JIT export behavior for downstream use. |

## lerobot-eval.yaml

| Field | Details |
| --- | --- |
| Purpose | OSMO LeRobot evaluation for HuggingFace or AzureML model sources, with optional registration. |
| Source YAML path | `evaluation/sil/workflows/osmo/lerobot-eval.yaml` |
| Primary parameters and overrides | `default-values.policy_repo_id` (empty), `default-values.policy_type` (`act`), `default-values.dataset_repo_id` (empty), `default-values.eval_episodes` (`"10"`), `default-values.eval_batch_size` (`"10"`), `default-values.record_video` (`"false"`), `default-values.mlflow_enable` (`"false"`), `default-values.register_model` (empty), `default-values.blob_storage_container` (`datasets`). |
| Typical submit path | `evaluation/sil/scripts/submit-osmo-lerobot-eval.sh` |
| Usage notes | This is the canonical LeRobot OSMO evaluation template. |

## Usage Notes

| Topic | Guidance |
| --- | --- |
| Source of truth | Use YAML files under `training/` and `evaluation/` as the canonical inventory. |
| Submission flow | Submit through the companion scripts listed above to resolve defaults from CLI, env vars, and Terraform outputs. |
| Runtime packaging | RL workflows use inline payload or dataset injection; choose based on payload size and reuse needs. |
| Related reference | See [Reference index](README.md) for adjacent script and artifact guides. |
