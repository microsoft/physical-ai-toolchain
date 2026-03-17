/**
 * # AKS Networking Resources
 *
 * This file creates the AKS-specific networking infrastructure for the SiL module including:
 * - AKS system node pool subnet
 * - GPU node pool subnets
 * - NSG associations for all AKS subnets
 * - NAT Gateway associations for outbound connectivity
 *
 * Note: Shared networking resources (VNet, NSG, NAT Gateway) are provided by the platform module.
 * Note: Pod subnets are not used with Azure CNI Overlay mode - pods use virtual IPs from pod_cidr.
 */

// ============================================================
// AKS System Node Pool Subnet
// ============================================================

resource "azurerm_subnet" "aks" {
  name                            = "snet-aks-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = var.virtual_network.name
  address_prefixes                = [var.aks_subnet_config.subnet_address_prefix_aks]
  default_outbound_access_enabled = !var.should_enable_nat_gateway
}

// ============================================================
// GPU Node Pool Subnets
// ============================================================

resource "azurerm_subnet" "gpu_node_pool" {
  for_each = var.node_pools

  name                            = "snet-aks-${each.key}-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  virtual_network_name            = var.virtual_network.name
  address_prefixes                = each.value.subnet_address_prefixes
  default_outbound_access_enabled = !var.should_enable_nat_gateway
}

// ============================================================
// NSG Associations
// ============================================================

resource "azurerm_subnet_network_security_group_association" "aks" {
  subnet_id                 = azurerm_subnet.aks.id
  network_security_group_id = var.network_security_group.id
}

resource "azurerm_subnet_network_security_group_association" "gpu_node_pool" {
  for_each = var.node_pools

  subnet_id                 = azurerm_subnet.gpu_node_pool[each.key].id
  network_security_group_id = var.network_security_group.id
}

// ============================================================
// NAT Gateway Associations
// ============================================================

resource "azurerm_subnet_nat_gateway_association" "aks" {
  count = var.should_enable_nat_gateway ? 1 : 0

  subnet_id      = azurerm_subnet.aks.id
  nat_gateway_id = var.nat_gateway.id
}

resource "azurerm_subnet_nat_gateway_association" "gpu_node_pool" {
  for_each = var.should_enable_nat_gateway ? var.node_pools : {}

  subnet_id      = azurerm_subnet.gpu_node_pool[each.key].id
  nat_gateway_id = var.nat_gateway.id
}
