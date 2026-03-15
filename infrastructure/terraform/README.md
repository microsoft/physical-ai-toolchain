---
title: Infrastructure as Code
description: Terraform configuration for Azure resources including AKS, Azure ML, storage, and OSMO backend services
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: how-to
keywords:
  - terraform
  - infrastructure
  - aks
---

Terraform configuration for the robotics reference architecture. Deploys Azure resources including AKS with GPU node pools, Azure ML workspace, storage, and OSMO backend services.

> [!NOTE]
> Complete configuration reference, architecture diagrams, and troubleshooting are in the [Infrastructure Deployment](../../docs/deploy/infrastructure.md) guide.

<!-- markdownlint-disable MD028 -->

> [!IMPORTANT]
> Private AKS clusters require VPN connectivity. Deploy [VPN Gateway](vpn/) before accessing cluster resources.

<!-- markdownlint-enable MD028 -->

## 🚀 Quick Start

```bash
cd deploy/001-iac
source ../000-prerequisites/az-sub-init.sh
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

## 📖 Documentation

| Guide                                                                     | Description                                          |
|---------------------------------------------------------------------------|------------------------------------------------------|
| [Infrastructure Deployment](../../docs/deploy/infrastructure.md)          | Configuration, variables, and deployment walkthrough |
| [Infrastructure Reference](../../docs/deploy/infrastructure-reference.md) | Architecture, module structure, and troubleshooting  |

## ➡️ Next Step

Deploy [VPN Gateway](vpn/) or proceed to [Cluster Setup](../002-setup/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
