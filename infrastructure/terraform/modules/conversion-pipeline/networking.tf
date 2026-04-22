/**
 * # Private Endpoints
 *
 * Private endpoints for the conversion storage account's blob and dfs subresources.
 * Reuses the platform module's shared private DNS zones (storage_blob, storage_dfs).
 */

locals {
  pe_enabled = var.should_enable_private_endpoint
}

resource "azurerm_private_endpoint" "blob" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-cpblob-${local.resource_name_suffix}"
  location            = local.location
  resource_group_name = var.resource_group.name
  subnet_id           = var.subnets.private_endpoints.id

  private_service_connection {
    name                           = "psc-cpblob-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-cpblob-${local.resource_name_suffix}"
    private_dns_zone_ids = [var.private_dns_zones.storage_blob.id]
  }
}

resource "azurerm_private_endpoint" "dfs" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-cpdfs-${local.resource_name_suffix}"
  location            = local.location
  resource_group_name = var.resource_group.name
  subnet_id           = var.subnets.private_endpoints.id

  private_service_connection {
    name                           = "psc-cpdfs-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = ["dfs"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-cpdfs-${local.resource_name_suffix}"
    private_dns_zone_ids = [var.private_dns_zones.storage_dfs.id]
  }
}
