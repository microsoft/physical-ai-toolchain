/**
 * # Azure Bastion Module
 *
 * Deploys Azure Bastion Standard with native tunneling, an optional Microsoft-compliant
 * Network Security Group, and an optional private Entra-authenticated Linux jumpbox VM.
 */

data "azurerm_client_config" "current" {}

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"

  current_user_principal_id = coalesce(
    var.current_user_oid,
    data.azurerm_client_config.current.object_id
  )

  should_assign_current_user_role = (
    var.should_deploy_jumpbox &&
    var.should_assign_current_user_admin_login &&
    !contains(var.jumpbox_admin_principal_ids, local.current_user_principal_id)
  )

  jumpbox_cloud_init = var.jumpbox_config.should_enable_cloud_init ? (
    var.jumpbox_config.custom_cloud_init != null ? var.jumpbox_config.custom_cloud_init : templatefile(
      "${path.module}/cloud-init.yaml.tftpl",
      {
        environment     = var.environment
        resource_prefix = var.resource_prefix
        instance        = var.instance
      }
    )
  ) : null

  tags = merge(var.tags, {
    module = "bastion"
  })
}

// ============================================================
// Azure Bastion Subnet
// ============================================================

// AzureBastionSubnet must use this exact name, be /26 or larger, and omit NAT Gateway or delegations
resource "azurerm_subnet" "bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = var.resource_group.name
  virtual_network_name = var.virtual_network.name
  address_prefixes     = [var.bastion_subnet_address_prefix]
}

// ============================================================
// Azure Bastion Network Security Group (Optional)
// ============================================================

resource "azurerm_network_security_group" "bastion" {
  count = var.should_create_bastion_nsg ? 1 : 0

  name                = "nsg-bas-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
  tags                = local.tags

  security_rule {
    name                       = "AllowHttpsInbound"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = var.bastion_allowed_source_address_prefix
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowGatewayManagerInbound"
    priority                   = 130
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "GatewayManager"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowAzureLoadBalancerInbound"
    priority                   = 140
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "AzureLoadBalancer"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "AllowBastionHostCommunication"
    priority                   = 150
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_ranges    = ["8080", "5701"]
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "VirtualNetwork"
  }

  security_rule {
    name                       = "AllowSshRdpOutbound"
    priority                   = 100
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_ranges    = ["22", "3389"]
    source_address_prefix      = "*"
    destination_address_prefix = "VirtualNetwork"
  }

  security_rule {
    name                       = "AllowAzureCloudOutbound"
    priority                   = 110
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "AzureCloud"
  }

  security_rule {
    name                       = "AllowBastionCommunication"
    priority                   = 120
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_ranges    = ["8080", "5701"]
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "VirtualNetwork"
  }

  security_rule {
    name                       = "AllowHttpOutbound"
    priority                   = 130
    direction                  = "Outbound"
    access                     = "Allow"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "*"
    destination_address_prefix = "Internet"
  }
}

resource "azurerm_subnet_network_security_group_association" "bastion" {
  count = var.should_create_bastion_nsg ? 1 : 0

  subnet_id                 = azurerm_subnet.bastion.id
  network_security_group_id = azurerm_network_security_group.bastion[0].id
}

// ============================================================
// Azure Bastion Public IP & Host
// ============================================================

resource "azurerm_public_ip" "bastion" {
  name                = "pip-bas-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = var.bastion_config.zones
  tags                = local.tags

  lifecycle {
    ignore_changes = [ip_tags]
  }
}

resource "azurerm_bastion_host" "main" {
  name                   = "bas-${local.resource_name_suffix}"
  location               = var.location
  resource_group_name    = var.resource_group.name
  sku                    = var.bastion_config.sku
  scale_units            = var.bastion_config.scale_units
  tunneling_enabled      = var.bastion_config.tunneling_enabled
  copy_paste_enabled     = var.bastion_config.copy_paste_enabled
  file_copy_enabled      = var.bastion_config.file_copy_enabled
  ip_connect_enabled     = var.bastion_config.ip_connect_enabled
  shareable_link_enabled = var.bastion_config.shareable_link_enabled
  zones                  = var.bastion_config.zones
  tags                   = local.tags

  ip_configuration {
    name                 = "bastionIpConfig"
    subnet_id            = azurerm_subnet.bastion.id
    public_ip_address_id = azurerm_public_ip.bastion.id
  }

  depends_on = [
    azurerm_subnet_network_security_group_association.bastion,
  ]
}
