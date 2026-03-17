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
// Storage Lifecycle Management Policy
// ============================================================

resource "azurerm_storage_management_policy" "main" {
  storage_account_id = azurerm_storage_account.main.id

  // Rule 1: Delete raw ROS bags after retention period
  rule {
    name    = "delete-raw-bags"
    enabled = var.should_enable_raw_bags_lifecycle_policy

    filters {
      prefix_match = ["raw/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.raw_bags_retention_days
      }
    }
  }

  // Rule 2: Tier converted datasets to cool storage
  rule {
    name    = "tier-converted-datasets-to-cool"
    enabled = var.should_enable_converted_datasets_lifecycle_policy

    filters {
      prefix_match = ["converted/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = var.converted_datasets_cool_tier_days
      }
    }
  }

  // Rule 3: Tier validation reports to cool then archive
  rule {
    name    = "tier-reports-to-cool-then-archive"
    enabled = var.should_enable_reports_lifecycle_policy

    filters {
      prefix_match = ["reports/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = var.reports_cool_tier_days
        tier_to_archive_after_days_since_modification_greater_than = var.reports_archive_tier_days
      }
    }
  }

  // Note: No lifecycle policy for checkpoints/ prefix — model checkpoints retained indefinitely in Hot tier
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
