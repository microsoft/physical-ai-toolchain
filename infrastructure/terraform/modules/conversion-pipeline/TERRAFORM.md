---
title: Conversion Pipeline Module
description: The conversion pipeline reuses the platform-owned ADLS Gen2 data-lake account (stdl...) for raw -> converted storage. This module owns only the Event Grid system topic + subscription that route BlobCreated events to the conversion subscriber, an in-account dead-letter container, and the Fabric capacity + workspace.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-28
ms.topic: reference
---

<!-- BEGIN_TF_DOCS -->
The conversion pipeline reuses the platform-owned ADLS Gen2 data-lake
account (stdl...) for raw -> converted storage. This module owns only the
Event Grid system topic + subscription that route BlobCreated events to the
conversion subscriber, an in-account dead-letter container, and the Fabric
capacity + workspace.

## Requirements

| Name      | Version         |
|-----------|-----------------|
| terraform | >= 1.9.8, < 2.0 |
| azurerm   | >= 4.51.0       |
| fabric    | 1.3.0           |

## Providers

| Name      | Version   |
|-----------|-----------|
| azurerm   | >= 4.51.0 |
| fabric    | 1.3.0     |
| terraform | n/a       |

## Resources

| Name                                                                                                                                                                                            | Type        |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| [azurerm_eventgrid_system_topic.blob](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/eventgrid_system_topic)                                                   | resource    |
| [azurerm_eventgrid_system_topic_event_subscription.raw_blob_created](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/eventgrid_system_topic_event_subscription) | resource    |
| [azurerm_fabric_capacity.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/fabric_capacity)                                                                 | resource    |
| [azurerm_role_assignment.eventgrid_dlq_writer](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                                                 | resource    |
| [azurerm_role_assignment.fabric_sp_data_lake_contributor](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                                      | resource    |
| [azurerm_storage_container.event_grid_dlq](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/storage_container)                                                   | resource    |
| [fabric_workspace.this](https://registry.terraform.io/providers/microsoft/fabric/1.3.0/docs/resources/workspace)                                                                                | resource    |
| [terraform_data.defer_fabric_capacity_created](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data)                                                          | resource    |
| [terraform_data.defer_fabric_capacity_existing](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data)                                                         | resource    |
| [fabric_capacity.created](https://registry.terraform.io/providers/microsoft/fabric/1.3.0/docs/data-sources/capacity)                                                                            | data source |
| [fabric_capacity.existing](https://registry.terraform.io/providers/microsoft/fabric/1.3.0/docs/data-sources/capacity)                                                                           | data source |

## Inputs

| Name                                      | Description                                                                                                                                                                                                    | Type                                                          | Default                               | Required |
|-------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|---------------------------------------|:--------:|
| data\_lake\_storage\_account              | Platform-owned ADLS Gen2 data-lake account (stdl...) used as the durable raw -> converted store                                                                                                                | ```object({ id = string name = string })```                   | n/a                                   |   yes    |
| environment                               | Environment for all resources in this module: dev, staging, or prod                                                                                                                                            | `string`                                                      | n/a                                   |   yes    |
| resource\_group                           | Resource group object containing name, id, and location                                                                                                                                                        | ```object({ id = string name = string location = string })``` | n/a                                   |   yes    |
| resource\_prefix                          | Prefix for all resources in this module                                                                                                                                                                        | `string`                                                      | n/a                                   |   yes    |
| conversion\_subscriber\_url               | Optional webhook URL for the downstream conversion subscriber. When null, the subscription is created without a webhook destination (DLQ-only) until the conversion compute lands                              | `string`                                                      | `null`                                |    no    |
| fabric\_admin\_members                    | Entra UPNs or object IDs that should be granted Fabric capacity administration                                                                                                                                 | `list(string)`                                                | `[]`                                  |    no    |
| fabric\_capacity\_sku                     | SKU for the Fabric capacity (F2 through F2048). Only used when should\_create\_fabric\_capacity is true                                                                                                        | `string`                                                      | `"F2"`                                |    no    |
| fabric\_workspace\_sp\_object\_id         | Object ID of the Fabric workspace service principal. When provided, the SP is granted Storage Blob Data Contributor on the platform data-lake account                                                          | `string`                                                      | `null`                                |    no    |
| instance                                  | Instance identifier for naming resources: 001, 002, etc                                                                                                                                                        | `string`                                                      | `"001"`                               |    no    |
| location                                  | Override location for module resources. Defaults to var.resource\_group.location when null                                                                                                                     | `string`                                                      | `null`                                |    no    |
| raw\_blob\_suffix\_filters                | Suffix filters used by the Event Grid subscription's advanced\_filter.string\_ends\_with on the raw container                                                                                                  | `list(string)`                                                | ```[ ".bag", ".bag.zst", ".mcap" ]``` |    no    |
| should\_create\_fabric\_capacity          | Whether to provision a new Fabric capacity                                                                                                                                                                     | `bool`                                                        | `true`                                |    no    |
| should\_create\_fabric\_workspace         | Whether to provision a Fabric workspace bound to the Fabric capacity. The workspace's capacity\_id is resolved at apply time from a deferred data "fabric\_capacity" lookup keyed on the capacity display name | `bool`                                                        | `true`                                |    no    |
| should\_enable\_event\_grid\_dead\_letter | Whether to enable an Event Grid dead-letter destination backed by an in-account container                                                                                                                      | `bool`                                                        | `true`                                |    no    |

## Outputs

| Name                        | Description                                                                                   |
|-----------------------------|-----------------------------------------------------------------------------------------------|
| event\_grid\_dlq\_container | Event Grid dead-letter container on the platform data-lake account. Null when DLQ is disabled |
| event\_grid\_subscription   | Event Grid subscription for raw BlobCreated events                                            |
| event\_grid\_topic          | Event Grid system topic on the platform data-lake account                                     |
| fabric\_capacity            | Microsoft Fabric capacity. Null when an existing capacity is reused                           |
| fabric\_workspace           | Microsoft Fabric workspace bound to the conversion capacity                                   |
<!-- END_TF_DOCS -->

<!-- markdownlint-disable MD036 -->
*🤖 Auto-generated by [terraform-docs](https://terraform-docs.io/) — do not edit manually.*
<!-- markdownlint-enable MD036 -->
