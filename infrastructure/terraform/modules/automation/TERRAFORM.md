---
title: Azure Automation Module
description: Creates an Azure Automation Account with a scheduled PowerShell runbook for automated startup of AKS clusters and PostgreSQL servers.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-07
ms.topic: reference
---

<!-- BEGIN_TF_DOCS -->
Creates an Azure Automation Account with a scheduled PowerShell runbook
for automated startup of AKS clusters and PostgreSQL servers.

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

| Name                                                                                                                                                                   | Type     |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| [azurerm_automation_account.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/automation_account)                                  | resource |
| [azurerm_automation_job_schedule.start_resources](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/automation_job_schedule)             | resource |
| [azurerm_automation_powershell72_module.az_accounts](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/automation_powershell72_module)   | resource |
| [azurerm_automation_powershell72_module.az_postgresql](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/automation_powershell72_module) | resource |
| [azurerm_automation_runbook.start_resources](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/automation_runbook)                       | resource |
| [azurerm_automation_schedule.morning_startup](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/automation_schedule)                     | resource |
| [azurerm_role_assignment.aks_contributor](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                             | resource |
| [azurerm_role_assignment.postgresql_contributor](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                      | resource |

## Inputs

| Name                  | Description                                                                                      | Type                                                                             | Default                                                                                                                     | Required |
|-----------------------|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|:--------:|
| aks\_cluster          | AKS cluster object containing id and name for startup and RBAC assignment                        | ```object({ id = string name = string })```                                      | n/a                                                                                                                         |   yes    |
| environment           | Environment for all resources in this module: dev, test, or prod                                 | `string`                                                                         | n/a                                                                                                                         |   yes    |
| location              | Location for all resources in this module                                                        | `string`                                                                         | n/a                                                                                                                         |   yes    |
| resource\_group       | Resource group object containing name, id, and location                                          | ```object({ id = string name = string location = string })```                    | n/a                                                                                                                         |   yes    |
| resource\_prefix      | Prefix for all resources in this module                                                          | `string`                                                                         | n/a                                                                                                                         |   yes    |
| runbook\_script\_path | Path to PowerShell runbook script file                                                           | `string`                                                                         | n/a                                                                                                                         |   yes    |
| instance              | Instance identifier for naming resources: 001, 002, etc                                          | `string`                                                                         | `"001"`                                                                                                                     |    no    |
| postgresql\_server    | PostgreSQL server object containing id and name for startup and RBAC assignment (null to skip)   | ```object({ id = string name = string })```                                      | `null`                                                                                                                      |    no    |
| schedule\_config      | Schedule configuration for startup runbook including start time (HH:MM), week days, and timezone | ```object({ start_time = string week_days = list(string) timezone = string })``` | ```{ "start_time": "08:00", "timezone": "UTC", "week_days": [ "Monday", "Tuesday", "Wednesday", "Thursday", "Friday" ] }``` |    no    |
| tags                  | Tags to apply to all resources created by this module                                            | `map(string)`                                                                    | `{}`                                                                                                                        |    no    |

## Outputs

| Name                | Description                         |
|---------------------|-------------------------------------|
| automation\_account | Automation account resource details |
| runbook             | Runbook resource details            |
| schedule            | Schedule resource details           |
<!-- END_TF_DOCS -->

<!-- markdownlint-disable MD036 -->
*🤖 Auto-generated by [terraform-docs](https://terraform-docs.io/) — do not edit manually.*
<!-- markdownlint-enable MD036 -->
