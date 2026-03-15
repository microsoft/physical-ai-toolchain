---
title: VPN Gateway
description: Point-to-site VPN connectivity for secure access to the private AKS cluster
author: Microsoft Robotics-AI Team
ms.date: 2026-02-23
ms.topic: how-to
keywords:
  - vpn
  - point-to-site
  - private-cluster
---

Point-to-site VPN for secure remote access to the private AKS cluster and Azure services. Required for kubectl access, OSMO UI, and other cluster-internal endpoints.

> [!NOTE]
> Complete VPN configuration including authentication options, client setup, and site-to-site configuration is in the [VPN Gateway Configuration](../../../docs/infrastructure/vpn.md) guide.

## 🚀 Quick Start

```bash
cd deploy/001-iac/vpn
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

## 📖 Documentation

| Guide                                                    | Description                                               |
|----------------------------------------------------------|-----------------------------------------------------------|
| [VPN Gateway Configuration](../../../docs/infrastructure/vpn.md) | Authentication options, client setup, and troubleshooting |

## ➡️ Next Step

Proceed to [Cluster Setup](../../002-setup/).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
