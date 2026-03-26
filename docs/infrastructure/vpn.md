---
sidebar_position: 7
title: VPN Gateway Configuration
description: Point-to-site and site-to-site VPN setup for private AKS cluster access
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: how-to
keywords:
  - vpn
  - point-to-site
  - site-to-site
  - private-access
---

Point-to-Site and Site-to-Site VPN connectivity for secure remote access to the private AKS cluster and Azure services.

> [!NOTE]
> Part of the [Deployment Guide](README.md). Return there for navigation and deployment order.

<!-- markdownlint-disable-next-line MD028 -->

> [!IMPORTANT]
> **Required for default configuration.** With `should_enable_private_aks_cluster = true` (the default), you must deploy this VPN Gateway and connect before running `kubectl` commands or [cluster setup](cluster-setup.md) scripts. Without VPN, the private cluster endpoint is not accessible.
>
> To skip VPN, set `should_enable_private_aks_cluster = false` in your `terraform.tfvars` for a public AKS control plane.

## 📋 Prerequisites

* Platform infrastructure deployed (`cd infrastructure/terraform && terraform apply`)
* Terraform 1.5+ installed
* Core variables matching parent deployment (`environment`, `resource_prefix`, `location`)

## 🚀 Quick Start

```bash
cd infrastructure/terraform/vpn

# Configure
cp terraform.tfvars.example terraform.tfvars
# Edit: environment, resource_prefix, location (must match 001-iac)

terraform init && terraform apply
```

Deployment takes 20-30 minutes for the VPN Gateway.

## ⚙️ Configuration

| Variable                                 | Description                  | Default                |
|------------------------------------------|------------------------------|------------------------|
| `gateway_subnet_address_prefix`          | GatewaySubnet CIDR (min /27) | `10.0.3.0/27`          |
| `vpn_gateway_config.sku`                 | Gateway SKU                  | `VpnGw1AZ`             |
| `vpn_gateway_config.client_address_pool` | P2S client IP range          | `["192.168.200.0/24"]` |
| `aad_auth_config.should_enable`          | Enable Azure AD auth         | `true`                 |

Non-AZ VPN Gateway SKUs are being deprecated by Azure. Use the AZ equivalents (`VpnGw1AZ`, `VpnGw2AZ`, `VpnGw3AZ`) to avoid portal warnings and unplanned SKU updates outside Terraform.

## 🔐 Authentication Options

### Azure AD (Recommended)

Enabled by default. Users authenticate with their Azure AD credentials via the Azure VPN Client.

```hcl
aad_auth_config = {
  should_enable = true
}
```

### Certificate

For environments without Azure AD integration:

```hcl
aad_auth_config = {
  should_enable = false
}
root_certificate_public_data = "MIIC5jCCAc6g..." # Base64-encoded cert
```

## 💻 VPN Client Setup

### Install Azure VPN Client

| Platform           | Installation                                                                                                                      |
|--------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| Windows            | [Microsoft Store](https://apps.microsoft.com/detail/9NP355QT2SQB)                                                                 |
| macOS              | [App Store](https://apps.apple.com/us/app/azure-vpn-client/id1553936137)                                                          |
| Ubuntu 20.04/22.04 | [Microsoft Docs](https://learn.microsoft.com/azure/vpn-gateway/point-to-site-entra-vpn-client-linux#install-the-azure-vpn-client) |

### Download VPN Configuration

1. Open the [Azure Portal](https://portal.azure.com)
2. Navigate to your Virtual Network Gateway resource:
   * Search for "Virtual network gateways" in the portal search bar
   * Select the gateway matching your deployment (e.g., `vgw-<resource_prefix>-<environment>-<instance>`)
3. Select **Point-to-site configuration** from the left menu
4. Click **Download VPN client** button
5. Save and extract the downloaded ZIP file

### Import Configuration

1. Open the Azure VPN Client application
2. Click the **+** (Import) button in the bottom left
3. Navigate to the extracted ZIP folder
4. Open the `AzureVPN` folder
5. Select `azurevpnconfig_aad.xml` (for Azure AD authentication)
6. Click **Save**

### Connect

1. Select the imported connection profile
2. Click **Connect**
3. Authenticate with your Azure AD credentials when prompted
4. Verify connection status shows "Connected"

Once connected, you can access private endpoints including OSMO UI, PostgreSQL, and Redis.

## 🏢 Site-to-Site VPN

Connect on-premises networks:

```hcl
vpn_site_connections = [{
  name                 = "on-prem-datacenter"
  address_spaces       = ["10.100.0.0/16"]
  gateway_ip_address   = "203.0.113.10"
  shared_key_reference = "datacenter-key"
}]

vpn_site_shared_keys = {
  "datacenter-key" = "your-preshared-key"
}
```

## 🔗 Related

* [Infrastructure Deployment](infrastructure.md) — Main infrastructure documentation
* [Private DNS](dns.md) — Private DNS for OSMO UI (requires VPN)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
