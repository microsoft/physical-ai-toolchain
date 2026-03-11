---
title: Deployment Guide
description: Infrastructure deployment and cluster configuration for the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: overview
keywords:
  - deployment
  - infrastructure
  - terraform
  - aks
  - osmo
---

End-to-end deployment of Azure infrastructure and Kubernetes services for the robotics reference architecture. This guide covers prerequisites, Terraform provisioning, VPN access, cluster configuration, and teardown.

## 📖 Deployment Guides

| Guide                                                   | Description                                                              |
|---------------------------------------------------------|--------------------------------------------------------------------------|
| [Prerequisites](prerequisites.md)                       | Azure subscription initialization and resource provider registration     |
| [Infrastructure Deployment](infrastructure.md)          | Terraform configuration for AKS, Azure ML, storage, and backend services |
| [Infrastructure Reference](infrastructure-reference.md) | Architecture, module structure, outputs, and troubleshooting             |
| [VPN Gateway](vpn.md)                                   | Point-to-site and site-to-site VPN for private cluster access            |
| [Private DNS](dns.md)                                   | DNS zone setup for OSMO UI access                                        |
| [Cluster Automation](automation.md)                     | Scheduled start/stop automation for cost management                      |
| [Cluster Setup](cluster-setup.md)                       | Kubernetes service deployment and OSMO configuration                     |
| [Cluster Operations](cluster-setup-advanced.md)         | Accessing OSMO, troubleshooting, and optional scripts                    |
| [Cleanup and Destroy](cleanup.md)                       | Remove cluster components and destroy Azure infrastructure               |

## 📋 Deployment Order

1. [Prerequisites](prerequisites.md) — Azure CLI login, subscription setup (2 min)
2. [Infrastructure](infrastructure.md) — Terraform: AKS, ML workspace, storage (30-40 min)
3. [VPN Gateway](vpn.md) — VPN Gateway for private cluster access (20-30 min)
4. [Cluster Setup](cluster-setup.md) — GPU Operator, OSMO, AzureML extension (30 min)

> [!IMPORTANT]
> The default configuration deploys a **private AKS cluster**. The cluster API endpoint is not publicly accessible. You must deploy the VPN Gateway (step 3) and connect before running cluster setup scripts (step 4).
>
> **Skip step 3** if you set `should_enable_private_aks_cluster = false` in your Terraform configuration. See [Infrastructure Reference](infrastructure-reference.md) for network configuration options.

## 📖 Quick Reference

| Task                    | Guide                                           |
|-------------------------|-------------------------------------------------|
| Deploy Azure resources  | [Infrastructure Deployment](infrastructure.md)  |
| Configure VPN access    | [VPN Gateway](vpn.md)                           |
| Set up cluster services | [Cluster Setup](cluster-setup.md)               |
| Access OSMO UI          | [Cluster Operations](cluster-setup-advanced.md) |
| Remove components       | [Cleanup and Destroy](cleanup.md)               |
| Destroy infrastructure  | [Cleanup and Destroy](cleanup.md)               |

## 📖 Terminology

| Term    | Definition                                                                                          |
|---------|-----------------------------------------------------------------------------------------------------|
| Deploy  | Provision Azure infrastructure or install cluster components using Terraform or deployment scripts. |
| Setup   | Post-deploy configuration and access steps for the cluster and workloads.                           |
| Cleanup | Remove cluster components while keeping Azure infrastructure intact.                                |
| Destroy | Delete Azure infrastructure (Terraform destroy or resource group deletion).                         |

## 📚 Related Documentation

* [Contributing Guide](../contributing/README.md)
* [Cost Considerations](../contributing/cost-considerations.md)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
