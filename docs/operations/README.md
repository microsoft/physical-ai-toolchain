---
sidebar_position: 1
title: Operations Hub
description: Centralized guide for operating, monitoring, and troubleshooting the robotics reference architecture on Azure and NVIDIA infrastructure
author: Microsoft Robotics-AI Team
ms.date: 2026-03-09
ms.topic: overview
keywords:
  - operations
  - monitoring
  - troubleshooting
  - observability
  - robotics
  - azure
---

Centralized hub for operational documentation covering monitoring, troubleshooting, security configuration, and GPU management for Azure-NVIDIA robotics deployments.

## 📖 Operations Guides

| Guide                                                                     | Description                                                              |
|---------------------------------------------------------------------------|--------------------------------------------------------------------------|
| [Troubleshooting](troubleshooting.md)                                     | Symptom-based resolution for common deployment, GPU, and workflow errors |
| [Security Guide](security-guide.md)                                       | Security configuration inventory and deployment checklist                |
| [GPU Configuration](../reference/gpu-configuration.md)                    | Driver selection, MIG strategy, and GPU Operator configuration           |
| [AzureML Validation Job Debugging](./azureml-validation-job-debugging.md) | Debug AzureML extension and InstanceType validation failures             |
| [Deployment Validation](../contributing/deployment-validation.md)         | Post-deployment verification steps                                       |
| [Cost Considerations](../contributing/cost-considerations.md)             | Azure resource cost guidance                                             |

## 📋 Operational Overview

The reference architecture deploys configurable monitoring components through Terraform feature flags.

| Component                        | Purpose                            | Feature Flag                      |
|----------------------------------|------------------------------------|-----------------------------------|
| Log Analytics workspace          | Central log aggregation            | Always deployed                   |
| Application Insights             | Application performance monitoring | Always deployed                   |
| Azure Monitor workspace          | Prometheus metrics backend         | `should_deploy_monitor_workspace` |
| Managed Grafana                  | Visualization dashboards           | `should_deploy_grafana`           |
| Container Insights               | AKS container telemetry            | `should_deploy_dce`               |
| Prometheus data collection rules | Metric scraping configuration      | `should_deploy_dce` and `should_deploy_monitor_workspace` |
| Azure Monitor Private Link Scope | Private network monitoring         | `should_deploy_ampls`             |
| Data collection endpoint         | Private ingestion endpoint         | `should_deploy_dce`               |

> [!IMPORTANT]
> The default configuration deploys a **private AKS cluster**. Connect through the VPN Gateway before running any `kubectl` or Helm commands. See [VPN Gateway](../infrastructure/vpn.md) for setup instructions.

Container Insights data collection rules are created when `should_deploy_dce` is enabled. Prometheus metrics collection rules require both `should_deploy_dce` and `should_deploy_monitor_workspace`.

## 🔗 Related Resources

- [Deployment Guide](../infrastructure/README.md)
- [Contributing Guide](../contributing/README.md)
- [Architecture](../contributing/architecture.md)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
