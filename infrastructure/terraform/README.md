---
title: Infrastructure as Code
description: Terraform configuration for Azure resources including AKS, Azure ML, storage, and OSMO backend services
author: Microsoft Robotics-AI Team
ms.date: 2026-04-29
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

## ⚙️ AzureML network controls

Set `should_enable_aml_diagnostic_logs = true` in `terraform.tfvars` to create an AML workspace diagnostic setting that sends all AML resource logs to the platform Log Analytics workspace. The default is `false`.

Set `aml_managed_network_isolation_mode` explicitly for the AzureML workspace managed network. Allowed values are `Disabled`, `AllowInternetOutbound`, and `AllowOnlyApprovedOutbound`. This setting is independent from `should_enable_private_endpoint`, which still controls Azure private endpoints, private DNS zones, and related network topology.

Treat changes to `aml_managed_network_isolation_mode` as AzureML redeploy operations. AzureML does not support disabling managed network isolation after it is enabled, or switching between `AllowInternetOutbound` and `AllowOnlyApprovedOutbound` in place. Delete and recreate managed compute resources when enabling managed networking on an existing workspace; recreate the workspace for unsupported mode transitions.

```hcl
should_enable_aml_diagnostic_logs = true
aml_managed_network_isolation_mode = "AllowOnlyApprovedOutbound"
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
