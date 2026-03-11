---
title: Getting Started
description: Entry point for deploying the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: overview
keywords:
  - getting-started
  - quickstart
  - deployment
  - onboarding
---

Deploy the Physical AI Toolchain and submit your first training job. This hub guides you through setup, deployment, and verification.

## 🚀 Guides

| Guide                               | Description                                  |
|-------------------------------------|----------------------------------------------|
| [Quickstart](quickstart.md)         | 8-step path from clone to first training job |
| Architecture Overview (coming soon) | System topology, components, and data flow   |
| Glossary (coming soon)              | Term definitions for Azure, NVIDIA, and OSMO |

## ⏱️ Time and Cost

| Item                  | Estimate           |
|-----------------------|--------------------|
| Total deployment time | ~1.5-2 hours       |
| Quick validation cost | ~$25-50            |
| GPU VM rate           | ~$3.06/hour (A100) |

> [!NOTE]
> Run `terraform destroy` when finished to stop incurring costs. See [Cost Considerations](../contributing/cost-considerations.md) for detailed estimates.

## 📋 Prerequisites Summary

| Tool      | Version |
|-----------|---------|
| Terraform | ≥1.9.8  |
| Azure CLI | ≥2.65.0 |
| kubectl   | ≥1.31   |
| Helm      | ≥3.16   |
| Python    | ≥3.11   |

Azure subscription with Contributor + User Access Administrator roles, GPU quota for `Standard_NC24ads_A100_v4`, and an NVIDIA NGC account are required. See [Prerequisites](../contributing/prerequisites.md) for full details.

## 📚 Related Documentation

| Resource                                                      | Description                             |
|---------------------------------------------------------------|-----------------------------------------|
| [Contributing Guide](../contributing/README.md)               | Development workflow and code standards |
| [Deployment Guide](../../deploy/README.md)                    | Detailed deployment reference           |
| [Cost Considerations](../contributing/cost-considerations.md) | Pricing breakdown and optimization      |
