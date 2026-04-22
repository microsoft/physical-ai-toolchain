/**
 * # Diagnostic Settings
 *
 * Routes storage account and blob service diagnostics to the platform module's
 * Log Analytics workspace. Satisfies CKV2_AZURE_21. File, queue, and table
 * sub-services are not present on hierarchical namespace accounts, so only the
 * blob sub-service is wired here.
 */

resource "azurerm_monitor_diagnostic_setting" "storage_account" {
  count = var.should_enable_diagnostic_settings ? 1 : 0

  name                       = "diag-${local.storage_name}"
  target_resource_id         = azurerm_storage_account.this.id
  log_analytics_workspace_id = var.log_analytics_workspace.id

  enabled_metric {
    category = "Transaction"
  }
}

resource "azurerm_monitor_diagnostic_setting" "blob_service" {
  count = var.should_enable_diagnostic_settings ? 1 : 0

  name                       = "diag-${local.storage_name}-blob"
  target_resource_id         = "${azurerm_storage_account.this.id}/blobServices/default"
  log_analytics_workspace_id = var.log_analytics_workspace.id

  enabled_log {
    category = "StorageRead"
  }

  enabled_log {
    category = "StorageWrite"
  }

  enabled_log {
    category = "StorageDelete"
  }

  enabled_metric {
    category = "Transaction"
  }
}
