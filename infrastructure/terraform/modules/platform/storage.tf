/**
 * # Storage Resources
 *
 * This file creates the Storage Account for the Platform module including:
 * - Storage Account for ML workspace and general purpose
 * - Default container for ML workspace
 * - Private endpoints for blob and file services
 */

// ============================================================
// Storage Account
// ============================================================

resource "azurerm_storage_account" "main" {
  name                            = "st${var.resource_prefix}${var.environment}${var.instance}"
  location                        = var.resource_group.location
  resource_group_name             = var.resource_group.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  access_tier                     = "Hot"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = var.should_enable_storage_shared_access_key
  public_network_access_enabled   = var.should_enable_public_network_access
  allow_nested_items_to_be_public = false

  blob_properties {
    delete_retention_policy {
      days = 7
    }

    container_delete_retention_policy {
      days = 7
    }
  }
}

// ============================================================
// Storage Containers
// ============================================================

// Default container for ML workspace
resource "azurerm_storage_container" "ml_workspace" {
  name                  = "ml-workspace"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

// ============================================================
// Storage Private Endpoints
// ============================================================

// Blob Private Endpoint
resource "azurerm_private_endpoint" "storage_blob" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-blob-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-blob-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_storage_account.main.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "pdz-blob-${local.resource_name_suffix}"
    // Note: storage_blob zone is SHARED with AMPLS
    private_dns_zone_ids = [azurerm_private_dns_zone.core["storage_blob"].id]
  }
}

// File Private Endpoint
resource "azurerm_private_endpoint" "storage_file" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-file-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-file-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_storage_account.main.id
    subresource_names              = ["file"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-file-${local.resource_name_suffix}"
    private_dns_zone_ids = [azurerm_private_dns_zone.core["storage_file"].id]
  }
}
