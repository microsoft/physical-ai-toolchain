/**
 * # Conversion Pipeline Module
 *
 * The conversion pipeline reuses the platform-owned ADLS Gen2 data-lake
 * account (stdl...) for raw -> converted storage. This module owns only the
 * Event Grid system topic + subscription that route BlobCreated events to the
 * conversion subscriber, an in-account dead-letter container, and the Fabric
 * capacity + workspace.
 */

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"
  location             = coalesce(var.location, var.resource_group.location)
}

// ============================================================
// Event Grid Dead-Letter Container
// ============================================================

resource "azurerm_storage_container" "event_grid_dlq" {
  count = var.should_enable_event_grid_dead_letter ? 1 : 0

  name                  = "event-grid-dlq"
  storage_account_id    = var.data_lake_storage_account.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}
