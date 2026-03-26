/**
 * # Container Registry Resources
 *
 * This file creates the Azure Container Registry for the Platform module including:
 * - Premium SKU ACR (required for private endpoint support)
 * - Private endpoint for secure access
 * - ML identity role assignments
 *
 * Note: AKS AcrPull role assignment is in the SiL module (requires AKS kubelet identity)
 */

// ============================================================
// Azure Container Registry
// ============================================================

resource "azurerm_container_registry" "main" {
  name                          = "acr${var.resource_prefix}${var.environment}${var.instance}"
  location                      = var.resource_group.location
  resource_group_name           = var.resource_group.name
  sku                           = "Premium"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = var.should_enable_public_network_access
}

// ============================================================
// ACR Private Endpoint
// ============================================================

resource "azurerm_private_endpoint" "acr" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-acr-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-acr-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_container_registry.main.id
    subresource_names              = ["registry"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-acr-${local.resource_name_suffix}"
    private_dns_zone_ids = [azurerm_private_dns_zone.core["acr"].id]
  }
}
