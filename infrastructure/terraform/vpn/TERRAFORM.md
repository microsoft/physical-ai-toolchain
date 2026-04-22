---
title: VPN Gateway Standalone Configuration
description: Deploys VPN Gateway for Point-to-Site and Site-to-Site connectivity using data sources to reference existing platform infrastructure.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-21
ms.topic: reference
---

<!-- BEGIN_TF_DOCS -->
Deploys VPN Gateway for Point-to-Site and Site-to-Site connectivity
using data sources to reference existing platform infrastructure.

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

| Name                                                                                                                               | Type        |
|------------------------------------------------------------------------------------------------------------------------------------|-------------|
| [azurerm_resource_group.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/resource_group)   | data source |
| [azurerm_virtual_network.this](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs/data-sources/virtual_network) | data source |

## Modules

| Name | Source         | Version |
|------|----------------|---------|
| vpn  | ../modules/vpn | n/a     |

## Inputs

| Name                              | Description                                                                                                                                                                                                   | Type                                                                                                                                                                                                                                                                               | Default                         | Required |
|-----------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------|:--------:|
| environment                       | Environment for all resources in this module: dev, test, or prod                                                                                                                                              | `string`                                                                                                                                                                                                                                                                           | n/a                             |   yes    |
| location                          | Location for all resources in this module                                                                                                                                                                     | `string`                                                                                                                                                                                                                                                                           | n/a                             |   yes    |
| resource\_prefix                  | Prefix for all resources in this module                                                                                                                                                                       | `string`                                                                                                                                                                                                                                                                           | n/a                             |   yes    |
| aad\_auth\_config                 | Azure AD authentication configuration for P2S VPN. tenant\_id defaults to current Azure client tenant if not specified. Uses Microsoft-registered Azure VPN application by default. Requires OpenVPN protocol | ```object({ should_enable = bool tenant_id = optional(string, null) audience_id = optional(string, "c632b3df-fb67-4d84-bdcf-b95ad541b5c8") })```                                                                                                                                   | ```{ "should_enable": true }``` |    no    |
| gateway\_subnet\_address\_prefix  | Address prefix for the GatewaySubnet (minimum /27)                                                                                                                                                            | `string`                                                                                                                                                                                                                                                                           | `"10.0.3.0/27"`                 |    no    |
| instance                          | Instance identifier for naming resources: 001, 002, etc                                                                                                                                                       | `string`                                                                                                                                                                                                                                                                           | `"001"`                         |    no    |
| resource\_group\_name             | Existing resource group name containing foundational resources (Otherwise 'rg-{resource\_prefix}-{environment}-{instance}')                                                                                   | `string`                                                                                                                                                                                                                                                                           | `null`                          |    no    |
| root\_certificate\_name           | Name for the root certificate used in P2S authentication                                                                                                                                                      | `string`                                                                                                                                                                                                                                                                           | `"RoboticsVPNRootCert"`         |    no    |
| root\_certificate\_public\_data   | Base64-encoded public certificate data for P2S authentication (without BEGIN/END markers)                                                                                                                     | `string`                                                                                                                                                                                                                                                                           | `null`                          |    no    |
| virtual\_network\_name            | Existing virtual network name (Otherwise 'vnet-{resource\_prefix}-{environment}-{instance}')                                                                                                                  | `string`                                                                                                                                                                                                                                                                           | `null`                          |    no    |
| vpn\_gateway\_config              | VPN Gateway configuration including SKU, generation, P2S client address pool, and availability zones for the public IP                                                                                        | ```object({ sku = optional(string, "VpnGw1AZ") generation = optional(string, "Generation1") client_address_pool = optional(list(string), ["192.168.200.0/24"]) zones = optional(list(string), ["1", "2", "3"]) })```                                                               | `{}`                            |    no    |
| vpn\_site\_connections            | Site-to-site VPN site definitions for connecting on-premises networks                                                                                                                                         | ```list(object({ name = string address_spaces = list(string) shared_key_reference = string gateway_ip_address = optional(string) gateway_fqdn = optional(string) bgp_asn = optional(number) bgp_peering_address = optional(string) ike_protocol = optional(string, "IKEv2") }))``` | `[]`                            |    no    |
| vpn\_site\_default\_ipsec\_policy | Default IPsec policy for all S2S connections                                                                                                                                                                  | ```object({ dh_group = string ike_encryption = string ike_integrity = string ipsec_encryption = string ipsec_integrity = string pfs_group = string sa_datasize_kb = optional(number) sa_lifetime_seconds = optional(number) })```                                                  | `null`                          |    no    |
| vpn\_site\_shared\_keys           | Pre-shared keys for S2S VPN connections indexed by shared\_key\_reference                                                                                                                                     | `map(string)`                                                                                                                                                                                                                                                                      | `{}`                            |    no    |

## Outputs

| Name                     | Description                                 |
|--------------------------|---------------------------------------------|
| gateway\_subnet          | Gateway subnet details                      |
| local\_network\_gateways | Local network gateway details for each site |
| p2s\_connection\_info    | Point-to-Site VPN connection information    |
| site\_connections        | Site-to-Site VPN connection details         |
| vpn\_gateway             | VPN Gateway resource details                |
| vpn\_gateway\_public\_ip | Public IP address of the VPN Gateway        |
<!-- END_TF_DOCS -->

<!-- markdownlint-disable MD036 -->
*🤖 Auto-generated by [terraform-docs](https://terraform-docs.io/) — do not edit manually.*
<!-- markdownlint-enable MD036 -->
