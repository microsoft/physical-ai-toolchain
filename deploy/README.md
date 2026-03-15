---
title: Deploy
description: Deployment pipeline for the Azure NVIDIA robotics reference architecture
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: overview
keywords:
  - deployment
  - infrastructure
  - robotics
---

Orchestrator for the end-to-end deployment pipeline. Covers Azure subscription setup, Terraform infrastructure provisioning, and AKS cluster configuration with GPU operator and AzureML.

> [!NOTE]
> Complete deployment walkthrough, architecture overview, and troubleshooting are in the [Deployment Guide](../docs/infrastructure/README.md).

## 🚀 Quick Start

```bash
# 1. Prerequisites — Azure subscription and provider registration
cd deploy/000-prerequisites && source az-sub-init.sh

# 2. Infrastructure — Terraform provisioning
cd ../001-iac && cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply -var-file=terraform.tfvars

# 3. Cluster setup — GPU operator, AzureML, OSMO
cd ../002-setup
az aks get-credentials --resource-group <rg> --name <aks>
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
```

## 📖 Documentation

| Guide                                        | Description                                         |
|----------------------------------------------|-----------------------------------------------------|
| [Deployment Guide](../docs/infrastructure/README.md) | End-to-end deployment hub and architecture overview |
| [Prerequisites](000-prerequisites/)          | Azure subscription initialization                   |
| [Infrastructure](001-iac/)                   | Terraform configuration and provisioning            |
| [Cluster Setup](002-setup/)                  | AKS cluster configuration and extensions            |

## ➡️ Next Step

Start with [Prerequisites](000-prerequisites/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
