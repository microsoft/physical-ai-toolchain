---
title: Cluster Setup
description: AKS cluster configuration with NVIDIA GPU operator, KAI Scheduler, and AzureML extension
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: how-to
keywords:
  - cluster-setup
  - kubernetes
  - azureml
---

AKS cluster configuration for robotics workloads. Deploys NVIDIA GPU operator, KAI Scheduler, AzureML extension, and OSMO components onto the AKS cluster provisioned in the infrastructure phase.

> [!NOTE]
> Complete setup walkthrough, deployment scenarios, and troubleshooting are in the [Cluster Setup](../../docs/infrastructure/cluster-setup.md) guide.

## 🚀 Quick Start

```bash
az aks get-credentials --resource-group <rg> --name <aks>
kubectl cluster-info
```

Deployment order:

1. `./01-deploy-robotics-charts.sh` — GPU Operator, KAI Scheduler
2. `./02-deploy-azureml-extension.sh` — AzureML K8s extension, compute attach
3. `./03-deploy-osmo-control-plane.sh` — OSMO control plane
4. `./04-deploy-osmo-backend.sh` — OSMO backend services
5. `./05-deploy-dataviewer.sh` — Dataviewer container build and deploy

## 📖 Documentation

| Guide                                                                     | Description                                       |
|---------------------------------------------------------------------------|---------------------------------------------------|
| [Cluster Setup](../../docs/infrastructure/cluster-setup.md)               | Full setup walkthrough and deployment scenarios   |
| [Cluster Operations](../../docs/infrastructure/cluster-setup-advanced.md) | Advanced operations, scaling, and troubleshooting |

## ➡️ Next Step

See [Deployment Scenarios](../../docs/infrastructure/cluster-setup.md#-deployment-scenarios) for advanced configurations.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
