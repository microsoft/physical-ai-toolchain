---
sidebar_position: 1
title: Evaluation Guide
description: Evaluate trained robotics policies in simulation and on physical hardware using Azure ML and NVIDIA OSMO
author: Microsoft Robotics-AI Team
ms.date: 2026-07-21
ms.topic: overview
keywords:
  - evaluation
  - robotics
  - Isaac Lab
  - LeRobot
  - OSMO
  - Azure ML
---

Evaluate trained robotics policies with offline replay, Isaac software-in-the-loop, separate target-policy-hardware HiL, Azure Machine Learning, or NVIDIA OSMO workflows.

## 📖 Evaluation Guides

| Guide                                                  | Description                                              |
|--------------------------------------------------------|----------------------------------------------------------|
| [LeRobot ACT Policy Evaluation](lerobot-evaluation.md) | Run LeRobot ACT policies locally with ROS2 deployment    |
| [OSMO Evaluation Workflows](osmo-evaluation.md)        | Execute Isaac Lab and LeRobot evaluation via NVIDIA OSMO |
| [HiL Evaluation](hil-evaluation.md)                    | Run local target-hardware or T3 no-command HiL           |

## ⚖️ Evaluation Comparison

| Mode | Policy location | Simulator location | Infrastructure |
|------|-----------------|--------------------|----------------|
| Offline replay | Development compute | None | Local files |
| Software-in-the-loop | Development compute | Development compute | Local process |
| T0 HiL | Deployment-representative host | Development workstation | ROS 2 and Docker |
| T3 orchestrated HiL | Local K3s workload | No simulator in current gate | OSMO and K3s |

## 🚀 Quick Start

LeRobot local evaluation:

```bash
python lerobot/scripts/eval.py \
  --policy.path=<path-to-checkpoint> \
  -p lerobot/configs/policy/act.yaml
```

Tier 0 HiL command pairs:

```bash
docker build --platform linux/amd64 \
  --file evaluation/hil/docker/Dockerfile.policy-host \
  --tag physical-ai-hil-policy-host:local \
  .
```

See [Hardware-in-the-Loop Evaluation](hil-evaluation.md) for the direct policy-host and simulator-host commands.

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
