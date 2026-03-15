---
sidebar_position: 9
title: Cluster Automation
description: Scheduled start and stop automation for AKS cluster cost management
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: reference
keywords:
  - automation
  - scheduled
  - start-stop
  - cost-management
---

Azure Automation account for scheduled infrastructure operations. Runs PowerShell runbooks to manage infrastructure resources, such as starting PostgreSQL and AKS at the beginning of business hours to reduce costs.

> [!NOTE]
> Part of the [Deployment Guide](README.md). Return there for navigation and deployment order.

## 📋 Prerequisites

* Platform infrastructure deployed (`cd infrastructure/terraform && terraform apply`)
* Core variables matching parent deployment (`environment`, `resource_prefix`, `location`)

## 🚀 Usage

```bash
cd infrastructure/terraform/automation

# Configure schedule and resources
# Edit terraform.tfvars with your schedule

terraform init && terraform apply
```

## ⚙️ Configuration

Example `terraform.tfvars`:

```hcl
environment     = "dev"
location        = "westus3"
resource_prefix = "rob"
instance        = "001"

should_start_postgresql = true

schedule_config = {
  start_time = "13:00"
  week_days  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
  timezone   = "UTC"
}
```

## 📦 Resources Created

* Azure Automation Account with system-assigned managed identity
* PowerShell 7.2 runbook for starting resources
* Weekly schedule with configurable days and start time
* Role assignments for AKS and PostgreSQL management

## 🔗 Related

* [Infrastructure Deployment](infrastructure.md) — Main infrastructure documentation

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
