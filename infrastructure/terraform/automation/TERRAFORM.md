---
title: Azure Automation Standalone Configuration
description: Deploys Azure Automation Account with scheduled runbook to start AKS cluster and PostgreSQL server every morning. Uses data sources to reference existing platform infrastructure.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-07
ms.topic: reference
---

<!-- BEGIN_TF_DOCS -->
Deploys Azure Automation Account with scheduled runbook to start
AKS cluster and PostgreSQL server every morning.
Uses data sources to reference existing platform infrastructure.

## Requirements

| Name      | Version         |
|-----------|-----------------|
| terraform | >= 1.9.8, < 2.0 |
| azurerm   | >= 4.51.0       |

## Providers

| Name    | Version   |
|---------|-----------|
| azurerm | >= 4.51.0 |

## Resources

| Name                                                                                                                                                     | Type        |
|----------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| [azurerm_kubernetes_cluster.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/kubernetes_cluster)                 | data source |
| [azurerm_postgresql_flexible_server.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/postgresql_flexible_server) | data source |
| [azurerm_resource_group.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/resource_group)                         | data source |

## Modules

| Name       | Source                | Version |
|------------|-----------------------|---------|
| automation | ../modules/automation | n/a     |

## Inputs

| Name                      | Description                                                                                      | Type                                                                             | Default                                                                                                                         | Required |
|---------------------------|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|:--------:|
| environment               | Environment for all resources in this module: dev, test, or prod                                 | `string`                                                                         | n/a                                                                                                                             |   yes    |
| location                  | Location for all resources in this module                                                        | `string`                                                                         | n/a                                                                                                                             |   yes    |
| resource\_prefix          | Prefix for all resources in this module                                                          | `string`                                                                         | n/a                                                                                                                             |   yes    |
| aks\_cluster\_name        | Override AKS cluster name (Otherwise 'aks-{resource\_prefix}-{environment}-{instance}')          | `string`                                                                         | `null`                                                                                                                          |    no    |
| instance                  | Instance identifier for naming resources: 001, 002, etc                                          | `string`                                                                         | `"001"`                                                                                                                         |    no    |
| postgresql\_name          | Override PostgreSQL server name (Otherwise 'psql-{resource\_prefix}-{environment}-{instance}')   | `string`                                                                         | `null`                                                                                                                          |    no    |
| resource\_group\_name     | Existing resource group name (Otherwise 'rg-{resource\_prefix}-{environment}-{instance}')        | `string`                                                                         | `null`                                                                                                                          |    no    |
| schedule\_config          | Schedule configuration for startup runbook including start time (HH:MM), week days, and timezone | ```object({ start_time = string week_days = list(string) timezone = string })``` | ```{ "start_time": "13:00", "timezone": "Etc/UTC", "week_days": [ "Monday", "Tuesday", "Wednesday", "Thursday", "Friday" ] }``` |    no    |
| should\_start\_postgresql | Whether to include PostgreSQL in the startup sequence                                            | `bool`                                                                           | `true`                                                                                                                          |    no    |

## Outputs

| Name                | Description                                                               |
|---------------------|---------------------------------------------------------------------------|
| automation\_account | Automation account resource details including id, name, and principal\_id |
| runbook             | Runbook resource details                                                  |
| schedule            | Schedule resource details including name, week\_days, and timezone        |
<!-- END_TF_DOCS -->

<!-- markdownlint-disable MD036 -->
*🤖 Auto-generated by [terraform-docs](https://terraform-docs.io/) — do not edit manually.*
<!-- markdownlint-enable MD036 -->
