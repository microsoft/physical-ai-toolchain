---
title: Dataviewer Module
description: Deploys the dataviewer application on Azure Container Apps with networking, identity, and app-level resources.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-07
ms.topic: reference
---

<!-- BEGIN_TF_DOCS -->
Deploys the dataviewer application on Azure Container Apps with networking, identity, and app-level resources.

Resources deployed:

- Container Apps Environment with optional VNet integration
- Backend (FastAPI) and Frontend (nginx + React) container apps
- User-assigned managed identity for ACR and Storage access
- Optional Entra ID app registration for public access mode

Supports internal (VNet/VPN) and external (public) deployment modes.

## Requirements

| Name      | Version         |
|-----------|-----------------|
| terraform | >= 1.9.8, < 2.0 |
| azuread   | >= 3.0.2        |
| azurerm   | >= 4.51.0       |
| random    | >= 3.6.0        |

## Providers

| Name    | Version   |
|---------|-----------|
| azuread | >= 3.0.2  |
| azurerm | >= 4.51.0 |
| random  | >= 3.6.0  |

## Resources

| Name                                                                                                                                                                                          | Type        |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------|
| [azuread_application.dataviewer](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/application)                                                                 | resource    |
| [azuread_service_principal.dataviewer](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/resources/service_principal)                                                     | resource    |
| [azurerm_container_app.backend](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app)                                                                | resource    |
| [azurerm_container_app.frontend](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app)                                                               | resource    |
| [azurerm_container_app_environment.main](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/container_app_environment)                                           | resource    |
| [azurerm_private_dns_a_record.container_apps_wildcard](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/private_dns_a_record)                                  | resource    |
| [azurerm_private_dns_zone.container_apps](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/private_dns_zone)                                                   | resource    |
| [azurerm_private_dns_zone_virtual_network_link.container_apps](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/private_dns_zone_virtual_network_link)         | resource    |
| [azurerm_role_assignment.dataviewer_acr_pull](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                                                | resource    |
| [azurerm_role_assignment.dataviewer_data_lake_blob](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                                          | resource    |
| [azurerm_role_assignment.dataviewer_storage_blob](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/role_assignment)                                            | resource    |
| [azurerm_subnet.container_apps](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/subnet)                                                                       | resource    |
| [azurerm_subnet_nat_gateway_association.container_apps](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/subnet_nat_gateway_association)                       | resource    |
| [azurerm_subnet_network_security_group_association.container_apps](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/subnet_network_security_group_association) | resource    |
| [azurerm_user_assigned_identity.dataviewer](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/resources/user_assigned_identity)                                           | resource    |
| [random_uuid.dataviewer_role_admin](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/uuid)                                                                      | resource    |
| [random_uuid.dataviewer_role_annotator](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/uuid)                                                                  | resource    |
| [random_uuid.dataviewer_role_viewer](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/uuid)                                                                     | resource    |
| [random_uuid.dataviewer_scope_id](https://registry.terraform.io/providers/hashicorp/random/latest/docs/resources/uuid)                                                                        | resource    |
| [azuread_client_config.current](https://registry.terraform.io/providers/hashicorp/azuread/latest/docs/data-sources/client_config)                                                             | data source |

## Inputs

| Name                             | Description                                                                                                                                                      | Type                                                              | Default                                                      | Required |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------|--------------------------------------------------------------|:--------:|
| container\_registry              | ACR from platform module                                                                                                                                         | ```object({ id = string name = string login_server = string })``` | n/a                                                          |   yes    |
| environment                      | Environment for all resources in this module: dev, test, or prod                                                                                                 | `string`                                                          | n/a                                                          |   yes    |
| location                         | Location for all resources in this module                                                                                                                        | `string`                                                          | n/a                                                          |   yes    |
| log\_analytics\_workspace        | Log Analytics workspace from platform module                                                                                                                     | ```object({ id = string workspace_id = string })```               | n/a                                                          |   yes    |
| network\_security\_group         | NSG from platform module                                                                                                                                         | ```object({ id = string })```                                     | n/a                                                          |   yes    |
| resource\_group                  | Resource group object containing name, id, and location                                                                                                          | ```object({ id = string name = string location = string })```     | n/a                                                          |   yes    |
| resource\_prefix                 | Prefix for all resources in this module                                                                                                                          | `string`                                                          | n/a                                                          |   yes    |
| storage\_account                 | Storage account from platform module                                                                                                                             | ```object({ id = string name = string })```                       | n/a                                                          |   yes    |
| virtual\_network                 | Virtual network from platform module                                                                                                                             | ```object({ id = string name = string })```                       | n/a                                                          |   yes    |
| backend\_cpu                     | CPU allocation for the backend container                                                                                                                         | `number`                                                          | `0.5`                                                        |    no    |
| backend\_image                   | Full image reference for the backend container (e.g., acr.azurecr.io/dataviewer-backend:latest). Leave empty to use a placeholder for initial IaC provisioning   | `string`                                                          | `""`                                                         |    no    |
| backend\_memory                  | Memory allocation for the backend container                                                                                                                      | `string`                                                          | `"1Gi"`                                                      |    no    |
| data\_lake\_storage\_account     | Data lake storage account from platform module. Null when data lake is disabled                                                                                  | ```object({ id = string name = string })```                       | `null`                                                       |    no    |
| dataviewer\_redirect\_uris       | SPA redirect URIs for MSAL.js authentication (local development)                                                                                                 | `list(string)`                                                    | ```[ "http://localhost:5173/", "http://localhost:5174/" ]``` |    no    |
| frontend\_cpu                    | CPU allocation for the frontend container                                                                                                                        | `number`                                                          | `0.25`                                                       |    no    |
| frontend\_image                  | Full image reference for the frontend container (e.g., acr.azurecr.io/dataviewer-frontend:latest). Leave empty to use a placeholder for initial IaC provisioning | `string`                                                          | `""`                                                         |    no    |
| frontend\_memory                 | Memory allocation for the frontend container                                                                                                                     | `string`                                                          | `"0.5Gi"`                                                    |    no    |
| instance                         | Instance identifier for naming resources: 001, 002, etc                                                                                                          | `string`                                                          | `"001"`                                                      |    no    |
| nat\_gateway                     | NAT Gateway from platform module. Null when NAT Gateway is disabled                                                                                              | ```object({ id = string })```                                     | `null`                                                       |    no    |
| should\_deploy\_dataviewer\_auth | Whether to create Entra ID app registration for public mode. Set to false for VNet-only mode                                                                     | `bool`                                                            | `false`                                                      |    no    |
| should\_enable\_internal         | Whether the Container Apps Environment uses internal load balancing (private access via VNet/VPN). When false, the environment is publicly accessible            | `bool`                                                            | `true`                                                       |    no    |
| should\_enable\_nat\_gateway     | Whether to associate NAT Gateway with the Container Apps subnet for outbound connectivity                                                                        | `bool`                                                            | `true`                                                       |    no    |
| storage\_annotation\_container   | Name of the Azure Blob Storage container for annotations                                                                                                         | `string`                                                          | `"annotations"`                                              |    no    |
| storage\_dataset\_container      | Name of the Azure Blob Storage container for datasets                                                                                                            | `string`                                                          | `"datasets"`                                                 |    no    |
| subnet\_address\_prefix          | Address prefix for the Container Apps infrastructure subnet. Must be /21 or larger                                                                               | `string`                                                          | `"10.0.16.0/21"`                                             |    no    |

## Outputs

| Name                        | Description                                                   |
|-----------------------------|---------------------------------------------------------------|
| backend                     | Backend Container App details                                 |
| container\_app\_environment | Container Apps Environment details                            |
| dataviewer\_identity        | Dataviewer managed identity for external role assignments     |
| entra\_id                   | Entra ID app registration details. Null when auth is disabled |
| frontend                    | Frontend Container App details                                |
<!-- END_TF_DOCS -->

<!-- markdownlint-disable MD036 -->
*🤖 Auto-generated by [terraform-docs](https://terraform-docs.io/) — do not edit manually.*
<!-- markdownlint-enable MD036 -->
