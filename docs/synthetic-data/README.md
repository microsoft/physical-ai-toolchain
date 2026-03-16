---
sidebar_position: 1
title: Synthetic Data
description: Generate photorealistic training data from simulation using NVIDIA Cosmos world foundation models
author: Microsoft Robotics-AI Team
ms.date: 2026-03-14
ms.topic: overview
keywords:
  - synthetic data
  - SDG
  - NVIDIA Cosmos
  - sim-to-real
  - Cosmos Transfer
  - Cosmos Predict
  - Cosmos Reason
---

Generate photorealistic training data from simulation using NVIDIA Cosmos world foundation models. This section covers the SDG pipeline architecture, Cosmos model integration, and workflow submission.

## SDG Pipeline

The synthetic data generation pipeline chains three Cosmos capabilities:

| Stage    | Model               | Description                                         |
|----------|---------------------|-----------------------------------------------------|
| Transfer | Cosmos Transfer 2.5 | Convert simulation renders to photorealistic images |
| Predict  | Cosmos Predict 2.5  | Generate plausible future frame sequences           |
| Reason   | Cosmos Reason 2     | Assess and curate data for training quality         |

## 🏗️ Architecture

```text
synthetic-data/
├── workflows/           # OSMO and AzureML SDG job definitions
│   ├── osmo/            # OSMO workflow YAML (Jinja templates)
│   └── azureml/         # AzureML job YAML (commandJob schema)
├── cosmos/              # Cosmos model integration
│   ├── transfer/        # Cosmos Transfer 2.5
│   ├── predict/         # Cosmos Predict 2.5
│   ├── reason/          # Cosmos Reason 2
│   └── configs/         # Model configuration templates
├── examples/            # Pipeline examples
└── specifications/      # Domain specifications
```
