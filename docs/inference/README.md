---
title: Inference Guide
description: Run trained robotics policies in simulation and on physical hardware using Azure ML and NVIDIA OSMO
author: Microsoft Robotics-AI Team
ms.date: 2026-02-24
ms.topic: overview
keywords:
  - inference
  - robotics
  - Isaac Lab
  - LeRobot
  - OSMO
  - Azure ML
---

Deploy trained robotics policies using local environments, Azure ML compute, or NVIDIA OSMO workflows. This guide covers LeRobot ACT policy inference and OSMO-managed inference for Isaac Lab and LeRobot workloads.

## 📖 Inference Guides

| Guide                                                | Description                                             |
|------------------------------------------------------|---------------------------------------------------------|
| [LeRobot ACT Policy Inference](lerobot-inference.md) | Run LeRobot ACT policies locally with ROS2 deployment   |
| [OSMO Inference Workflows](osmo-inference.md)        | Execute Isaac Lab and LeRobot inference via NVIDIA OSMO |

## ⚖️ Inference Comparison

| Feature              | Local / Azure ML        | OSMO                        |
|----------------------|-------------------------|-----------------------------|
| Orchestration        | Manual or Azure ML jobs | OSMO workflow engine        |
| Checkpoint source    | MLflow, HuggingFace     | MLflow, Azure Blob, HTTP(S) |
| Supported frameworks | LeRobot                 | Isaac Lab, LeRobot          |
| GPU management       | User-managed            | KAI Scheduler               |
| Monitoring           | Local logs              | `osmo workflow logs`        |

## 🚀 Quick Start

LeRobot local inference:

```bash
python lerobot/scripts/eval.py \
  --policy.path=<path-to-checkpoint> \
  -p lerobot/configs/policy/act.yaml
```

OSMO inference submission:

```bash
osmo workflow submit \
  --file workflows/osmo/infer.yaml \
  --set checkpoint_uri=<checkpoint-uri>
```

## 📚 Related Documentation

- [Training Guide](../training/README.md)
- [MLflow Integration](../training/mlflow-integration.md)
- [Workflow Templates](../../workflows/README.md)
- [Scripts Reference](../reference/scripts.md)

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
