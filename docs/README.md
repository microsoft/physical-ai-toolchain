---
title: Documentation
description: Index of all documentation for the Physical AI Toolchain
author: Edge AI Team
ms.date: 2026-02-22
ms.topic: overview
keywords:
  - documentation
  - index
  - robotics
  - azure
---

Technical documentation for deploying, training, and operating robotics workloads on Azure with NVIDIA Isaac and OSMO. This index organizes every guide, reference, and walkthrough in the repository by topic so you can find what you need based on where you are in the workflow.

Documentation spans the full lifecycle — from provisioning Azure infrastructure with Terraform, through training reinforcement-learning policies with Isaac Lab and AzureML, to running inference on edge devices. Each section targets a specific audience and phase of the project.

## 👤 Audience Guide

| Role                   | Start here                                                                            |
|------------------------|---------------------------------------------------------------------------------------|
| First-time deployer    | Getting Started (coming soon), then [Deploy](../deploy/)                              |
| ML / Robotics engineer | Training (coming soon) and Inference (coming soon)                                    |
| Platform operator      | [Operations](operations/README.md) and [Security Guide](operations/security-guide.md) |
| Contributor            | [Contributing](contributing/README.md)                                                |

## 📖 Documentation Index

| Section                                | Description                                                                         | Status      |
|----------------------------------------|-------------------------------------------------------------------------------------|-------------|
| Getting Started                        | Environment setup, prerequisites, and first deployment walkthrough                  | Coming soon |
| [Deploy](../deploy/)                   | Infrastructure provisioning with Terraform, AKS cluster setup, and networking       | Available   |
| Training                               | Model training pipelines with Isaac Lab, AzureML jobs, and OSMO orchestration       | Coming soon |
| Inference                              | Serving trained policies for real-time control on edge and cloud                    | Coming soon |
| Workflows                              | AzureML and OSMO job templates, pipeline configuration, and submission scripts      | Coming soon |
| [Operations](operations/README.md)     | Monitoring, scaling, troubleshooting, and cost management for running clusters      | Available   |
| Security                               | Identity, networking, compliance, and hardening for production deployments          | Coming soon |
| [Reference](reference/README.md)       | CLI parameter tables, script usage, workflow templates, and configuration reference | Available   |
| [Contributing](contributing/README.md) | Contribution guidelines, PR process, deployment validation, and coding conventions  | Available   |

## 📄 Current Guides

Standalone guides available now. These cover common tasks and will move into their respective topic sections as the documentation structure expands.

| Guide                                                                   | Description                                                                                                         |
|-------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------|
| [AzureML Validation Job Debugging](azureml-validation-job-debugging.md) | Diagnosing and resolving AzureML validation job failures on AKS, including pod scheduling and resource quota issues |
| [LeRobot Inference](inference/lerobot-inference.md)                     | Running LeRobot inference workloads with pre-trained policies on Azure infrastructure                               |
| [MLflow Integration](training/mlflow-integration.md)                    | Configuring MLflow experiment tracking for SKRL training agents with automatic metric logging to Azure ML           |
| [Security Guide](operations/security-guide.md)                          | Security configuration inventory, deployment responsibilities, and hardening checklist for robotics workloads       |

## 🚀 Next Steps

* Review the [deployment guide](../deploy/README.md) for infrastructure provisioning and cluster setup
* Explore [MLflow Integration](training/mlflow-integration.md) to set up experiment tracking for training runs
* Read the [Contributing](contributing/README.md) guide to get involved with the project

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
