---
title: Scripts
description: Submission scripts for AzureML and OSMO training and inference pipelines.
author: Microsoft Robotics-AI Team
ms.date: 2026-03-08
ms.topic: reference
keywords:
  - scripts
  - submission
  - azureml
  - osmo
---

Submission scripts for training and inference workflows on Azure ML and OSMO platforms.

> [!NOTE]
> Full script documentation has moved to [Script Reference](../docs/reference/scripts.md) and [Script Examples](../docs/reference/scripts-examples.md).

## 🚀 Quick Start

```bash
# Azure ML training
./submit-azureml-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# OSMO training
./submit-osmo-training.sh --task Isaac-Velocity-Rough-Anymal-C-v0

# LeRobot behavioral cloning
./submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human
```

## 📜 Scripts

| Script                               | Purpose                                             |
|--------------------------------------|-----------------------------------------------------|
| `submit-azureml-training.sh`         | Package code and submit Azure ML training job       |
| `submit-azureml-validation.sh`       | Submit model validation job                         |
| `submit-azureml-lerobot-training.sh` | Submit LeRobot training to Azure ML                 |
| `submit-osmo-training.sh`            | Submit OSMO workflow (base64 payload)               |
| `submit-osmo-dataset-training.sh`    | Submit OSMO workflow (dataset folder injection)     |
| `submit-osmo-lerobot-training.sh`    | Submit LeRobot behavioral cloning training          |
| `submit-osmo-lerobot-inference.sh`   | Submit LeRobot inference/evaluation                 |
| `run-lerobot-pipeline.sh`            | End-to-end train → evaluate → register pipeline    |

## 📚 Related Documentation

* [Script Reference](../docs/reference/scripts.md) — CLI arguments and configuration
* [Script Examples](../docs/reference/scripts-examples.md) — Submission examples
* [Reference Hub](../docs/reference/README.md) — All reference documentation
<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
