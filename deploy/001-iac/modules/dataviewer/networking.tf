/**
 * # Networking Resources
 *
 * Creates the Container Apps infrastructure subnet and associated network resources.
 * Container Apps requires a dedicated subnet with /21 or larger address space.
 * Internal mode creates a Private DNS zone for VNet resolution.
 */

// ============================================================
// Container Apps Subnet
// ============================================================

resource "azurerm_subnet" "container_apps" {
  name                            = "snet-cae-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = var.virtual_network.name
  address_prefixes                = [var.subnet_address_prefix]
  default_outbound_access_enabled = !var.should_enable_nat_gateway

  delegation {
    name = "Microsoft.App.environments"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

// ============================================================
// NSG Association
// ============================================================

resource "azurerm_subnet_network_security_group_association" "container_apps" {
  subnet_id                 = azurerm_subnet.container_apps.id
  network_security_group_id = var.network_security_group.id
}

// ============================================================
// NAT Gateway Association
// ============================================================

resource "azurerm_subnet_nat_gateway_association" "container_apps" {
  count = var.should_enable_nat_gateway && var.nat_gateway != null ? 1 : 0

  subnet_id      = azurerm_subnet.container_apps.id
  nat_gateway_id = var.nat_gateway.id
}

// ============================================================
// Private DNS Zone (Internal Mode)
// ============================================================

resource "azurerm_private_dns_zone" "container_apps" {
  count               = var.should_enable_internal ? 1 : 0
  name                = azurerm_container_app_environment.main.default_domain
  resource_group_name = var.resource_group.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "container_apps" {
  count                 = var.should_enable_internal ? 1 : 0
  name                  = "vnet-link-cae"
  resource_group_name   = var.resource_group.name
  private_dns_zone_name = azurerm_private_dns_zone.container_apps[0].name
  virtual_network_id    = var.virtual_network.id
  registration_enabled  = false
}

resource "azurerm_private_dns_a_record" "container_apps_wildcard" {
  count               = var.should_enable_internal ? 1 : 0
  name                = "*"
  zone_name           = azurerm_private_dns_zone.container_apps[0].name
  resource_group_name = var.resource_group.name
  ttl                 = 300
  records             = [azurerm_container_app_environment.main.static_ip_address]
}
