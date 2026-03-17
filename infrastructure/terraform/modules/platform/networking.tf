/**
 * # Networking Resources
 *
 * This file creates the networking infrastructure for the Platform module including:
 * - Network Security Group for traffic filtering
 * - Virtual Network with address space
 * - Subnets for main workloads and private endpoints
 * - NAT Gateway with public IP for outbound connectivity
 *
 * Note: AKS-specific subnets (nodes, pods) are created in the SiL module
 */

// Network Security Group
resource "azurerm_network_security_group" "main" {
  name                = "nsg-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
}

// Virtual Network
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  address_space       = [var.virtual_network_config.address_space]
}

// Main Subnet - General workloads
resource "azurerm_subnet" "main" {
  name                            = "snet-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = azurerm_virtual_network.main.name
  address_prefixes                = [var.virtual_network_config.subnet_address_prefix_main]
  default_outbound_access_enabled = !var.should_enable_nat_gateway
}

// Private Endpoints Subnet (conditional - only created when private endpoints are enabled)
resource "azurerm_subnet" "private_endpoints" {
  count = local.pe_enabled ? 1 : 0

  name                            = "snet-pe-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = azurerm_virtual_network.main.name
  address_prefixes                = [var.virtual_network_config.subnet_address_prefix_pe]
  default_outbound_access_enabled = !var.should_enable_nat_gateway
}

// NSG Associations
resource "azurerm_subnet_network_security_group_association" "main" {
  subnet_id                 = azurerm_subnet.main.id
  network_security_group_id = azurerm_network_security_group.main.id
}

resource "azurerm_subnet_network_security_group_association" "private_endpoints" {
  count = local.pe_enabled ? 1 : 0

  subnet_id                 = azurerm_subnet.private_endpoints[0].id
  network_security_group_id = azurerm_network_security_group.main.id
}

// NAT Gateway Public IP
resource "azurerm_public_ip" "nat_gateway" {
  count = var.should_enable_nat_gateway ? 1 : 0

  name                = "pip-ng-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = ["1"]

  lifecycle {
    ignore_changes = [ip_tags]
  }
}

// NAT Gateway
resource "azurerm_nat_gateway" "main" {
  count = var.should_enable_nat_gateway ? 1 : 0

  name                    = "ng-${local.resource_name_suffix}"
  location                = var.resource_group.location
  resource_group_name     = var.resource_group.name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  zones                   = ["1"]
}

// NAT Gateway Public IP Association
resource "azurerm_nat_gateway_public_ip_association" "main" {
  count = var.should_enable_nat_gateway ? 1 : 0

  nat_gateway_id       = azurerm_nat_gateway.main[0].id
  public_ip_address_id = azurerm_public_ip.nat_gateway[0].id
}

// NAT Gateway Subnet Associations
resource "azurerm_subnet_nat_gateway_association" "main" {
  count = var.should_enable_nat_gateway ? 1 : 0

  subnet_id      = azurerm_subnet.main.id
  nat_gateway_id = azurerm_nat_gateway.main[0].id
}

// ============================================================
// DNS Private Resolver
// ============================================================
// Enables clients to resolve Azure Private DNS zones.
// Required for accessing private AKS clusters and other private endpoints
// from VPN clients or on-premises networks.

resource "azurerm_subnet" "resolver" {
  count = local.pe_enabled && var.virtual_network_config.subnet_address_prefix_resolver != null ? 1 : 0

  name                            = "snet-resolver-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = azurerm_virtual_network.main.name
  address_prefixes                = [var.virtual_network_config.subnet_address_prefix_resolver]
  default_outbound_access_enabled = !var.should_enable_nat_gateway

  delegation {
    name = "Microsoft.Network.dnsResolvers"
    service_delegation {
      name    = "Microsoft.Network/dnsResolvers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_resolver" "main" {
  count = length(azurerm_subnet.resolver)

  name                = "dnspr-${local.resource_name_suffix}"
  resource_group_name = var.resource_group.name
  location            = var.resource_group.location
  virtual_network_id  = azurerm_virtual_network.main.id
}

resource "azurerm_private_dns_resolver_inbound_endpoint" "main" {
  count = length(azurerm_subnet.resolver)

  name                    = "ipe-${local.resource_name_suffix}"
  private_dns_resolver_id = azurerm_private_dns_resolver.main[0].id
  location                = var.resource_group.location

  ip_configurations {
    private_ip_allocation_method = "Dynamic"
    subnet_id                    = azurerm_subnet.resolver[0].id
  }
}

// ============================================================
// VNet DNS Configuration
// ============================================================
// Configures the virtual network to use the Private Resolver for DNS.
// This enables VPN clients and on-premises networks to automatically
// resolve private endpoints.

resource "azurerm_virtual_network_dns_servers" "main" {
  count = length(azurerm_private_dns_resolver_inbound_endpoint.main)

  virtual_network_id = azurerm_virtual_network.main.id
  dns_servers        = [azurerm_private_dns_resolver_inbound_endpoint.main[0].ip_configurations[0].private_ip_address]
}
