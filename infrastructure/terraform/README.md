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
> Complete configuration reference, architecture diagrams, and troubleshooting are in the [Infrastructure Deployment](../../docs/infrastructure/infrastructure.md) guide.

<!-- markdownlint-disable MD028 -->

> [!IMPORTANT]
> Private AKS clusters require VPN connectivity. Deploy [VPN Gateway](vpn/) before accessing cluster resources.

<!-- markdownlint-enable MD028 -->

## 🚀 Quick Start

```bash
cd infrastructure/terraform
source prerequisites/az-sub-init.sh
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

## ⚙️ Optional AML diagnostics

Set `should_enable_aml_diagnostic_logs = true` in `terraform.tfvars` to create an AML workspace diagnostic setting that sends all AML resource logs to the platform Log Analytics workspace. The default is `false`.

```hcl
should_enable_aml_diagnostic_logs = true
```

## 📖 Documentation

| Guide                                                                             | Description                                          |
|-----------------------------------------------------------------------------------|------------------------------------------------------|
| [Infrastructure Deployment](../../docs/infrastructure/infrastructure.md)          | Configuration, variables, and deployment walkthrough |
| [Infrastructure Reference](../../docs/infrastructure/infrastructure-reference.md) | Architecture, module structure, and troubleshooting  |
| [Terraform Reference](TERRAFORM.md)                                               | Auto-generated inputs, outputs, and resources        |

## ➡️ Next Step

Deploy [VPN Gateway](vpn/) or proceed to [Cluster Setup](../setup/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
