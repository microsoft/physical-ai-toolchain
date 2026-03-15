---
sidebar_position: 1
title: Evaluation Guide
description: Evaluate trained robotics policies in simulation and on physical hardware using Azure ML and NVIDIA OSMO
author: Microsoft Robotics-AI Team
ms.date: 2026-03-14
ms.topic: overview
keywords:
  - evaluation
  - robotics
  - Isaac Lab
  - LeRobot
  - OSMO
  - Azure ML
---

Evaluate trained robotics policies using local environments, Azure ML compute, or NVIDIA OSMO workflows. This guide covers LeRobot ACT policy evaluation and OSMO-managed evaluation for Isaac Lab and LeRobot workloads.

## 📖 Evaluation Guides

| Guide                                                    | Description                                               |
|----------------------------------------------------------|-----------------------------------------------------------|
| [LeRobot ACT Policy Evaluation](lerobot-evaluation.md)   | Run LeRobot ACT policies locally with ROS2 deployment     |
| [OSMO Evaluation Workflows](osmo-evaluation.md)          | Execute Isaac Lab and LeRobot evaluation via NVIDIA OSMO  |

## ⚖️ Evaluation Comparison

| Feature              | Local / Azure ML        | OSMO                        |
|----------------------|-------------------------|-----------------------------|
| Orchestration        | Manual or Azure ML jobs | OSMO workflow engine        |
| Checkpoint source    | MLflow, HuggingFace     | MLflow, Azure Blob, HTTP(S) |
| Supported frameworks | LeRobot                 | Isaac Lab, LeRobot          |
| GPU management       | User-managed            | KAI Scheduler               |
| Monitoring           | Local logs              | `osmo workflow logs`        |

## 🚀 Quick Start

LeRobot local evaluation:

```bash
python lerobot/scripts/eval.py \
  --policy.path=<path-to-checkpoint> \
  -p lerobot/configs/policy/act.yaml
```

OSMO evaluation submission:

```bash
osmo workflow submit \
  --file evaluation/sil/workflows/osmo/eval.yaml \
  --set checkpoint_uri=<checkpoint-uri>
```

## 📚 Related Documentation

- [Training Guide](../training/README.md)
- [MLflow Integration](../training/mlflow-integration.md)
- [Workflow Templates](https://github.com/microsoft/physical-ai-toolchain/blob/main/workflows/README.md)
- [Scripts Reference](../reference/scripts.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
