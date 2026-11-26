---
title: Azure Bastion
description: Standalone Azure Bastion Standard and private Linux jumpbox deployment for private AKS and OSMO access
author: Microsoft Robotics-AI Team
ms.date: 2026-11-25
ms.topic: how-to
keywords:
  - bastion
  - jumpbox
  - private-cluster
---

Standalone Azure Bastion Standard with native client tunneling and an optional private
Entra-authenticated Linux jumpbox for accessing private AKS, OSMO, and Dataviewer endpoints
without a VPN client.

> [!NOTE]
> Complete setup, Entra ID SSH login, port-forwarding workflows, and VPN comparison details are in the [Azure Bastion Access](../../../docs/infrastructure/bastion.md) guide.

## 🚀 Quick Start

```bash
cd infrastructure/terraform/bastion
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

## 📖 Documentation

| Guide                                                           | Description                                                       |
|-----------------------------------------------------------------|-------------------------------------------------------------------|
| [Azure Bastion Access](../../../docs/infrastructure/bastion.md) | Prerequisites, Entra ID SSH, port-forwarding, and troubleshooting |
| [Terraform Reference](TERRAFORM.md)                             | Auto-generated inputs, outputs, and resources                     |

## ➡️ Next Step

Connect with [connect-bastion.sh](../../setup/connect-bastion.sh) or proceed to [Cluster Setup](../../setup/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
