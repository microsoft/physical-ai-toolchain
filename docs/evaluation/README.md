---
sidebar_position: 1
title: Evaluation Guide
description: Evaluate trained robotics policies in simulation and on physical hardware using Azure ML and NVIDIA OSMO
author: Microsoft Robotics-AI Team
ms.date: 2026-09-25
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

| Guide                                                  | Description                                              |
|--------------------------------------------------------|----------------------------------------------------------|
| [LeRobot Policy Evaluation](lerobot-evaluation.md)     | Evaluate LeRobot policies locally, on Azure ML, or OSMO  |
| [OSMO Evaluation Workflows](osmo-evaluation.md)        | Execute Isaac Lab and LeRobot evaluation via NVIDIA OSMO |
| [HiL Evaluation](hil-evaluation.md)                    | Run CPU and independently no-command HiL gates           |

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
uv run python evaluation/sil/scripts/run-local-lerobot-eval.py \
  --policy-path <path-to-checkpoint> \
  --dataset-dir <path-to-lerobot-dataset> \
  --episodes 5 \
  --output-dir outputs/local-eval
```

OSMO evaluation of an Azure ML model against a verified Viewer release:

```bash
evaluation/sil/scripts/submit-osmo-lerobot-eval.sh \
  --from-aml-model \
  --model-name <model-name> \
  --model-version <model-version> \
  --from-blob-dataset \
  --storage-account <storage-account> \
  --storage-container datasets \
  --blob-prefix "exports/releases/<dataset-id>/<release-id>" \
  --dataset-trust verified \
  --mlflow-enable
```

Follow [Record Episodes for a Verified Experiment](../recipes/data-collection/record-to-verified-experiment.md) to create the release and run the complete T1-to-T2 workflow.

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
