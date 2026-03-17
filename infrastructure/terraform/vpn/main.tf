/**
 * VPN Gateway Standalone Configuration
 *
 * Deploys VPN Gateway for Point-to-Site and Site-to-Site connectivity
 * using data sources to reference existing platform infrastructure.
 */
locals {
  resource_group_name  = coalesce(var.resource_group_name, "rg-${var.resource_prefix}-${var.environment}-${var.instance}")
  virtual_network_name = coalesce(var.virtual_network_name, "vnet-${var.resource_prefix}-${var.environment}-${var.instance}")
}

data "azurerm_resource_group" "this" {
  name = local.resource_group_name
}

data "azurerm_virtual_network" "this" {
  name                = local.virtual_network_name
  resource_group_name = local.resource_group_name
}

// ============================================================
// VPN Gateway Module
// ============================================================

module "vpn" {
  source = "../modules/vpn"

  // Core variables
  environment     = var.environment
  resource_prefix = var.resource_prefix
  location        = var.location
  instance        = var.instance
  tags            = {}

  resource_group = {
    id       = data.azurerm_resource_group.this.id
    name     = data.azurerm_resource_group.this.name
    location = data.azurerm_resource_group.this.location
  }

  // Dependencies from data sources
  virtual_network = {
    id   = data.azurerm_virtual_network.this.id
    name = data.azurerm_virtual_network.this.name
  }

  // VPN Gateway configuration
  gateway_subnet_address_prefix = var.gateway_subnet_address_prefix
  vpn_gateway_config            = var.vpn_gateway_config

  // P2S Certificate configuration
  root_certificate_name        = var.root_certificate_name
  root_certificate_public_data = var.root_certificate_public_data

  // P2S Azure AD configuration
  aad_auth_config = var.aad_auth_config

  // S2S VPN connections
  vpn_site_connections          = var.vpn_site_connections
  vpn_site_shared_keys          = var.vpn_site_shared_keys
  vpn_site_default_ipsec_policy = var.vpn_site_default_ipsec_policy
}
