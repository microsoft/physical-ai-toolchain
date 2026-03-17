/**
 * Private DNS Zone for OSMO UI Service
 *
 * Creates a private DNS zone for internal resolution of the OSMO UI service
 * running on an internal LoadBalancer within the AKS cluster.
 */
locals {
  resource_group_name  = coalesce(var.resource_group_name, "rg-${var.resource_prefix}-${var.environment}-${var.instance}")
  virtual_network_name = coalesce(var.virtual_network_name, "vnet-${var.resource_prefix}-${var.environment}-${var.instance}")
}

data "azurerm_virtual_network" "this" {
  name                = local.virtual_network_name
  resource_group_name = local.resource_group_name
}

resource "azurerm_private_dns_zone" "osmo" {
  name                = var.osmo_private_dns_zone_name
  resource_group_name = local.resource_group_name
}

resource "azurerm_private_dns_zone_virtual_network_link" "osmo" {
  name                  = "vnet-pzl-osmo-${var.resource_prefix}-${var.environment}-${var.instance}"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.osmo.name
  virtual_network_id    = data.azurerm_virtual_network.this.id
  registration_enabled  = false
}

resource "azurerm_private_dns_a_record" "osmo" {
  name                = var.osmo_hostname
  zone_name           = azurerm_private_dns_zone.osmo.name
  resource_group_name = local.resource_group_name
  ttl                 = 300
  records             = [var.osmo_loadbalancer_ip]
}
