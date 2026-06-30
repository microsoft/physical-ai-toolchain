---
sidebar_position: 1
title: Training Guide
description: Training workflows, experiment tracking, and ML pipeline documentation for the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-06-03
ms.topic: overview
keywords:
  - training
  - azureml
  - osmo
  - mlflow
  - lerobot
  - isaac lab
---

Training documentation for reinforcement learning with Isaac Lab and behavioral cloning with LeRobot. Both frameworks run on Azure ML and NVIDIA OSMO platforms.

## 📖 Training Guides

| Guide                                         | Description                                                          |
|-----------------------------------------------|----------------------------------------------------------------------|
| [Azure ML Training](azureml-training.md)      | Submit Isaac Lab and LeRobot training jobs to Azure ML               |
| [Experiment Tracking](experiment-tracking.md) | MLflow setup, model registration, checkpoint flows                   |
| [Isaac Lab Training](isaac-lab-training.md)   | RL training with SKRL and RSL-RL backends on Azure ML and OSMO       |
| [LeRobot Training](lerobot-training.md)       | Behavioral cloning with ACT and Diffusion policies                   |
| [MLflow Integration](mlflow-integration.md)   | SKRL metric logging internals, metric filtering, and troubleshooting |
| [OSMO Training](osmo-training.md)             | Submit distributed Isaac Lab training jobs through NVIDIA OSMO       |

## ⚖️ Platform Comparison

| Aspect              | Azure ML                 | OSMO                                    |
|---------------------|--------------------------|-----------------------------------------|
| Submission          | `az ml job create`       | `osmo workflow submit`                  |
| Orchestration       | Azure ML compute targets | OSMO workflow engine + KAI Scheduler    |
| Experiment tracking | MLflow (managed)         | MLflow (Azure ML backend)               |
| Dataset injection   | Azure ML datastores      | OSMO object storage or dataset upload   |
| Model registration  | `az ml model create`     | Via MLflow or post-training script      |
| Monitoring          | Azure ML Studio          | OSMO UI Dashboard                       |

## 🚀 Quick Start

Isaac Lab RL training on Azure ML:

```bash
training/rl/scripts/submit-azureml-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0
```

LeRobot behavioral cloning on OSMO:

```bash
training/il/scripts/submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human
```

## 📚 Related Documentation

- [Deployment Guide](../infrastructure/cluster-setup.md) for infrastructure setup
- [AzureML Workflows](azureml-training.md) for job template reference
- [OSMO Workflows](osmo-training.md) for workflow template reference
- [Scripts Reference](../reference/scripts.md) for CLI usage

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
