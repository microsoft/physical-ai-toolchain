/**
 * # VPN Module Outputs
 *
 * Typed object outputs for consumption by other modules.
 */

/*
 * VPN Gateway Outputs
 */

output "vpn_gateway" {
  description = "VPN Gateway resource details"
  value = {
    id         = azurerm_virtual_network_gateway.main.id
    name       = azurerm_virtual_network_gateway.main.name
    sku        = azurerm_virtual_network_gateway.main.sku
    generation = azurerm_virtual_network_gateway.main.generation
  }
}

output "vpn_gateway_public_ip" {
  description = "Public IP address of the VPN Gateway"
  value = {
    id         = azurerm_public_ip.vpn_gateway.id
    ip_address = azurerm_public_ip.vpn_gateway.ip_address
  }
}

output "gateway_subnet" {
  description = "Gateway subnet details"
  value = {
    id             = azurerm_subnet.gateway.id
    name           = azurerm_subnet.gateway.name
    address_prefix = azurerm_subnet.gateway.address_prefixes[0]
  }
}

/*
 * P2S Connection Info
 */

output "p2s_connection_info" {
  description = "Point-to-Site VPN connection information"
  value = {
    client_address_pool = var.vpn_gateway_config.client_address_pool
    protocols           = ["OpenVPN", "IkeV2"]
    gateway_public_ip   = azurerm_public_ip.vpn_gateway.ip_address
  }
}

/*
 * S2S Connection Outputs
 */

output "site_connections" {
  description = "Site-to-Site VPN connection details"
  value = try({
    for name, conn in azurerm_virtual_network_gateway_connection.sites : name => {
      id   = conn.id
      name = conn.name
    }
  }, {})
}

output "local_network_gateways" {
  description = "Local network gateway details for each site"
  value = try({
    for name, lgw in azurerm_local_network_gateway.sites : name => {
      id            = lgw.id
      name          = lgw.name
      address_space = lgw.address_space
    }
  }, {})
}
