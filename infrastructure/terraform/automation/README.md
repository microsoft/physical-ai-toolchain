---
title: Cluster Automation
description: Azure Automation Account for scheduled cluster operations and runbooks
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: reference
keywords:
  - automation
  - scheduled
  - start-stop
---

Azure Automation Account for scheduled cluster operations. Manages start/stop schedules and maintenance runbooks for the AKS cluster.

> [!NOTE]
> Complete automation configuration including schedule setup and runbook details is in the [Cluster Automation](../../../docs/infrastructure/automation.md) guide.

## 🚀 Quick Start

```bash
cd infrastructure/terraform/automation
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

## 📖 Documentation

| Guide                                                            | Description                                             |
|------------------------------------------------------------------|---------------------------------------------------------|
| [Cluster Automation](../../../docs/infrastructure/automation.md) | Schedule configuration, runbooks, and managed resources |

## ➡️ Next Step

Proceed to [Cluster Setup](../../setup/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
