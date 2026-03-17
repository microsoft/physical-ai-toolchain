---
sidebar_position: 2
title: Prerequisites
description: Azure subscription initialization and resource provider registration for robotics deployment
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: how-to
keywords:
  - prerequisites
  - azure
  - resource-providers
  - subscription
---

Azure CLI initialization and subscription setup required before Terraform deployments.

> [!NOTE]
> Part of the [Deployment Guide](README.md). Return there for navigation and deployment order.

## 📜 Scripts

| Script                        | Purpose                                      |
|-------------------------------|----------------------------------------------|
| `az-sub-init.sh`              | Azure login and `ARM_SUBSCRIPTION_ID` export |
| `register-azure-providers.sh` | Register required Azure resource providers   |

## 🚀 Usage

Source the initialization script to set `ARM_SUBSCRIPTION_ID` for Terraform:

```bash
source az-sub-init.sh
```

For a specific tenant:

```bash
source az-sub-init.sh --tenant your-tenant.onmicrosoft.com
```

For new Azure subscriptions or subscriptions that haven't deployed AKS, AzureML, or similar resources, register the required providers:

```bash
./register-azure-providers.sh
```

The script reads providers from `robotics-azure-resource-providers.txt` and waits for registration to complete. This is a one-time operation per subscription.

## ⚙️ What It Does

### az-sub-init.sh

1. Checks for existing Azure CLI session
2. Prompts for login if needed (optionally with tenant)
3. Exports `ARM_SUBSCRIPTION_ID` to current shell

The subscription ID is required by Terraform's Azure provider when not running in a managed identity context.

### register-azure-providers.sh

1. Reads required providers from `robotics-azure-resource-providers.txt`
2. Checks current registration state via Azure CLI
3. Registers unregistered providers
4. Polls until all providers reach `Registered` state

## ➡️ Next Step

After initialization, proceed to [Infrastructure Deployment](infrastructure.md) to deploy Azure resources.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
