/**
 * # Bastion Module Outputs
 *
 * Typed object outputs for Azure Bastion, optional NSG, optional jumpbox,
 * and operator connection helpers.
 */

output "bastion_host" {
  description = "Azure Bastion host resource details"
  value = {
    id                = azurerm_bastion_host.main.id
    name              = azurerm_bastion_host.main.name
    dns_name          = azurerm_bastion_host.main.dns_name
    sku               = azurerm_bastion_host.main.sku
    scale_units       = azurerm_bastion_host.main.scale_units
    tunneling_enabled = azurerm_bastion_host.main.tunneling_enabled
  }
}

output "bastion_public_ip" {
  description = "Public IP address details for Azure Bastion"
  value = {
    id         = azurerm_public_ip.bastion.id
    name       = azurerm_public_ip.bastion.name
    ip_address = azurerm_public_ip.bastion.ip_address
    zones      = azurerm_public_ip.bastion.zones
  }
}

output "bastion_subnet" {
  description = "AzureBastionSubnet resource details"
  value = {
    id             = azurerm_subnet.bastion.id
    name           = azurerm_subnet.bastion.name
    address_prefix = azurerm_subnet.bastion.address_prefixes[0]
  }
}

output "bastion_nsg" {
  description = "Azure Bastion Network Security Group details, or null when disabled"
  value = var.should_create_bastion_nsg ? {
    id   = azurerm_network_security_group.bastion[0].id
    name = azurerm_network_security_group.bastion[0].name
  } : null
}

output "jumpbox" {
  description = "Private Linux jumpbox VM details, or null when disabled"
  value = var.should_deploy_jumpbox ? {
    id                 = azurerm_linux_virtual_machine.jumpbox[0].id
    name               = azurerm_linux_virtual_machine.jumpbox[0].name
    private_ip_address = azurerm_network_interface.jumpbox[0].private_ip_address
    admin_username     = var.jumpbox_config.admin_username
    principal_id       = azurerm_linux_virtual_machine.jumpbox[0].identity[0].principal_id
    nic_id             = azurerm_network_interface.jumpbox[0].id
  } : null
}

output "connection_info" {
  description = "Operator connection metadata for connect-bastion.sh"
  value = {
    resource_group_name    = var.resource_group.name
    bastion_name           = azurerm_bastion_host.main.name
    bastion_dns_name       = azurerm_bastion_host.main.dns_name
    tunneling_enabled      = azurerm_bastion_host.main.tunneling_enabled
    jumpbox_id             = var.should_deploy_jumpbox ? azurerm_linux_virtual_machine.jumpbox[0].id : null
    jumpbox_name           = var.should_deploy_jumpbox ? azurerm_linux_virtual_machine.jumpbox[0].name : null
    jumpbox_private_ip     = var.should_deploy_jumpbox ? azurerm_network_interface.jumpbox[0].private_ip_address : null
    jumpbox_admin_username = var.should_deploy_jumpbox ? var.jumpbox_config.admin_username : null
  }
}
