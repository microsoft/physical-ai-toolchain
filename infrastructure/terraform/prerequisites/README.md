---
title: Prerequisites
description: Azure subscription initialization and resource provider registration
author: Microsoft Robotics-AI Team
ms.date: 2026-08-31
ms.topic: how-to
keywords:
  - prerequisites
  - azure
  - resource-providers
---

Azure subscription initialization and resource provider registration. Configures subscription context and registers required Azure resource providers for the robotics reference architecture.

> [!NOTE]
> Complete prerequisites including tooling requirements, version constraints, and Azure quota checks are in the [Prerequisites](../../docs/infrastructure/prerequisites.md) guide.

<!-- markdownlint-disable-next-line MD028 -->

> [!CAUTION]
> Existing AzureRM v4 deployments require the [AzureRM v5 migration procedure](../../../docs/infrastructure/azurerm-v5-migration.md) before registration and deployment. Do not substitute fresh-deployment commands for the migration procedure.

## 🚀 Quick Start

```bash
source az-sub-init.sh
```

## 📖 Documentation

| Guide                                                       | Description                                          |
|-------------------------------------------------------------|------------------------------------------------------|
| [Prerequisites](../../docs/infrastructure/prerequisites.md) | Full prerequisites, tooling, and Azure configuration |

## ➡️ Next Step

Proceed to [Infrastructure as Code](../README.md).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
