/**
 * # Conversion Pipeline Storage
 *
 * ADLS Gen2 storage account that backs the raw → converted ingest pipeline.
 * Hierarchical namespace is required so Microsoft Fabric can mount converted
 * data via OneLake shortcuts.
 */

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"
  storage_name         = "stcp${var.resource_prefix}${var.environment}${var.instance}"
  location             = coalesce(var.location, var.resource_group.location)
}

// ============================================================
// Storage Account (ADLS Gen2)
// ============================================================

resource "azurerm_storage_account" "this" {
  name                     = local.storage_name
  location                 = local.location
  resource_group_name      = var.resource_group.name
  account_tier             = "Standard"
  account_replication_type = var.storage_replication_type
  account_kind             = "StorageV2"
  access_tier              = "Hot"
  is_hns_enabled           = true
  min_tls_version          = "TLS1_2"

  // checkov:skip=CKV2_AZURE_40: Shared key gated behind should_enable_shared_key (default false). AzureML extension may require it temporarily; revisit per DR-03.
  shared_access_key_enabled       = var.should_enable_shared_key
  public_network_access_enabled   = var.should_enable_public_network_access
  allow_nested_items_to_be_public = false
  default_to_oauth_authentication = true

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices", "Logging", "Metrics"]
    ip_rules       = var.allowed_ip_rules
  }

  blob_properties {
    delete_retention_policy {
      days = var.blob_soft_delete_days
    }

    container_delete_retention_policy {
      days = var.container_soft_delete_days
    }

    versioning_enabled  = true
    change_feed_enabled = true
  }

  sas_policy {
    expiration_period = "07.00:00:00"
    expiration_action = "Log"
  }

  // checkov:skip=CKV_AZURE_33: Queue service unavailable on hierarchical namespace accounts.
  // checkov:skip=CKV_AZURE_206: Replication tier parameterized per environment (LRS dev, ZRS staging, GRS prod).
  // checkov:skip=CKV2_AZURE_1: Customer-managed key encryption tracked in follow-up WI-01.
  // checkov:skip=CKV2_AZURE_18: Customer-managed key with Key Vault tracked in follow-up WI-01.
  // checkov:skip=CKV2_AZURE_50: Immutability policy tracked in follow-up WI-01.

  lifecycle {
    prevent_destroy = true
  }
}

// ============================================================
// Storage Containers
// ============================================================

resource "azurerm_storage_container" "raw" {
  name                  = "raw"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "converted" {
  name                  = "converted"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "event_grid_dlq" {
  count = var.should_enable_event_grid_dead_letter ? 1 : 0

  name                  = "event-grid-dlq"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}
