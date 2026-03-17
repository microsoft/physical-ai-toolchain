/**
 * # VPN Gateway Module
 *
 * Deploys Azure VPN Gateway for Point-to-Site and Site-to-Site connectivity.
 * Creates GatewaySubnet within the platform's virtual network.
 */

data "azurerm_client_config" "current" {}

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"

  // Use provided tenant_id or fall back to current client config
  aad_tenant_id = coalesce(var.aad_auth_config.tenant_id, data.azurerm_client_config.current.tenant_id)

  // Determine authentication types based on configuration
  has_certificate_auth = var.root_certificate_public_data != null
  has_aad_auth         = var.aad_auth_config.enabled

  // Build vpn_auth_types list based on enabled authentication methods
  vpn_auth_types = compact([
    local.has_aad_auth ? "AAD" : "",
    local.has_certificate_auth ? "Certificate" : "",
  ])

  // Site name slugs for resource naming (alphanumeric only)
  site_name_slugs = {
    for site in var.vpn_site_connections :
    site.name => lower(replace(site.name, "/[^a-zA-Z0-9]/", ""))
  }

  tags = merge(var.tags, {
    module = "vpn"
  })
}

// ============================================================
// Gateway Subnet
// ============================================================

resource "azurerm_subnet" "gateway" {
  name                            = "GatewaySubnet"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = var.virtual_network.name
  address_prefixes                = [var.gateway_subnet_address_prefix]
  default_outbound_access_enabled = !var.should_enable_nat_gateway
}

// ============================================================
// Public IP for VPN Gateway
// ============================================================

resource "azurerm_public_ip" "vpn_gateway" {
  name                = "pip-vgw-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

// ============================================================
// VPN Gateway
// ============================================================

resource "azurerm_virtual_network_gateway" "main" {
  name                = "vgw-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name

  type     = "Vpn"
  vpn_type = "RouteBased"

  active_active = false
  enable_bgp    = false
  sku           = var.vpn_gateway_config.sku
  generation    = var.vpn_gateway_config.generation

  ip_configuration {
    name                          = "vnetGatewayConfig"
    public_ip_address_id          = azurerm_public_ip.vpn_gateway.id
    private_ip_address_allocation = "Dynamic"
    subnet_id                     = azurerm_subnet.gateway.id
  }

  // Point-to-Site VPN Configuration
  vpn_client_configuration {
    address_space = var.vpn_gateway_config.client_address_pool

    // OpenVPN always enabled; IKEv2 added when certificate auth is available
    vpn_client_protocols = local.has_certificate_auth ? ["OpenVPN", "IkeV2"] : ["OpenVPN"]

    // Specify auth types when using multiple methods or Azure AD
    vpn_auth_types = length(local.vpn_auth_types) > 0 ? local.vpn_auth_types : null

    // Azure AD authentication configuration
    aad_tenant   = local.has_aad_auth ? "https://login.microsoftonline.com/${local.aad_tenant_id}" : null
    aad_audience = local.has_aad_auth ? var.aad_auth_config.audience_id : null
    aad_issuer   = local.has_aad_auth ? "https://sts.windows.net/${local.aad_tenant_id}/" : null

    // Certificate authentication
    dynamic "root_certificate" {
      for_each = local.has_certificate_auth ? [1] : []
      content {
        name             = var.root_certificate_name
        public_cert_data = var.root_certificate_public_data
      }
    }
  }

  tags = local.tags

  timeouts {
    create = "60m"
    update = "60m"
    delete = "60m"
  }
}

// ============================================================
// Site-to-Site VPN - Local Network Gateways
// ============================================================

resource "azurerm_local_network_gateway" "sites" {
  for_each = { for site in var.vpn_site_connections : site.name => site }

  name                = "lgw-${local.site_name_slugs[each.key]}-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
  address_space       = each.value.address_spaces

  gateway_address = try(each.value.gateway_ip_address, null)
  gateway_fqdn    = try(each.value.gateway_fqdn, null)

  dynamic "bgp_settings" {
    for_each = each.value.bgp_asn != null ? [1] : []
    content {
      asn                 = each.value.bgp_asn
      bgp_peering_address = each.value.bgp_peering_address
    }
  }

  tags = local.tags
}

// ============================================================
// Site-to-Site VPN - Connections
// ============================================================

resource "azurerm_virtual_network_gateway_connection" "sites" {
  for_each = { for site in var.vpn_site_connections : site.name => site }

  name                = "vcn-${local.site_name_slugs[each.key]}-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name

  type                       = "IPsec"
  virtual_network_gateway_id = azurerm_virtual_network_gateway.main.id
  local_network_gateway_id   = azurerm_local_network_gateway.sites[each.key].id

  shared_key          = var.vpn_site_shared_keys[each.value.shared_key_reference]
  connection_protocol = try(each.value.ike_protocol, "IKEv2")

  // Optional IPsec policy
  dynamic "ipsec_policy" {
    for_each = var.vpn_site_default_ipsec_policy != null ? [var.vpn_site_default_ipsec_policy] : []
    content {
      dh_group         = ipsec_policy.value.dh_group
      ike_encryption   = ipsec_policy.value.ike_encryption
      ike_integrity    = ipsec_policy.value.ike_integrity
      ipsec_encryption = ipsec_policy.value.ipsec_encryption
      ipsec_integrity  = ipsec_policy.value.ipsec_integrity
      pfs_group        = ipsec_policy.value.pfs_group
      sa_datasize      = try(ipsec_policy.value.sa_datasize_kb, null)
      sa_lifetime      = try(ipsec_policy.value.sa_lifetime_seconds, null)
    }
  }

  tags = local.tags

  lifecycle {
    precondition {
      condition     = contains(keys(var.vpn_site_shared_keys), each.value.shared_key_reference)
      error_message = "Missing shared key '${each.value.shared_key_reference}' in vpn_site_shared_keys for site '${each.key}'."
    }
  }
}
