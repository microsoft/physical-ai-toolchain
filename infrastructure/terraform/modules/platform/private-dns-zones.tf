/**
 * # Private DNS Zones
 *
 * Centralized Private DNS Zone management for all services.
 * This file creates and manages all private DNS zones to prevent duplicate zone creation.
 *
 * Key Insight: `privatelink.blob.core.windows.net` is SHARED by both Storage Account and AMPLS.
 */

// ============================================================
// Core Private DNS Zones (conditional - created when PE enabled)
// Base: 6 zones, +1 AKS zone, +4 monitor zones (up to 11)
// ============================================================

resource "azurerm_private_dns_zone" "core" {
  for_each = local.pe_enabled ? local.core_dns_zones : {}

  name                = each.value
  resource_group_name = var.resource_group.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "core" {
  for_each = local.pe_enabled ? local.core_dns_zones : {}

  name                  = "vnet-link-${each.key}"
  resource_group_name   = var.resource_group.name
  private_dns_zone_name = azurerm_private_dns_zone.core[each.key].name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
}

// ============================================================
// Optional DNS Zones (OSMO Services)
// ============================================================

// PostgreSQL DNS Zone (conditional)
resource "azurerm_private_dns_zone" "postgresql" {
  count = var.should_deploy_postgresql ? 1 : 0

  name                = "privatelink.postgres.database.azure.com"
  resource_group_name = var.resource_group.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgresql" {
  count = var.should_deploy_postgresql ? 1 : 0

  name                  = "vnet-link-postgresql"
  resource_group_name   = var.resource_group.name
  private_dns_zone_name = azurerm_private_dns_zone.postgresql[0].name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
}

// Redis DNS Zone (conditional on deployment and PE enabled)
resource "azurerm_private_dns_zone" "redis" {
  count = var.should_deploy_redis && local.pe_enabled ? 1 : 0

  name                = "privatelink.redis.azure.net"
  resource_group_name = var.resource_group.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis" {
  count = var.should_deploy_redis && local.pe_enabled ? 1 : 0

  name                  = "vnet-link-redis"
  resource_group_name   = var.resource_group.name
  private_dns_zone_name = azurerm_private_dns_zone.redis[0].name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
}
