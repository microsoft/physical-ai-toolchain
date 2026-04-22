/**
 * # Storage Lifecycle Management
 *
 * Lifecycle rules for the conversion pipeline:
 * - Delete raw ROS bags after the configured retention window (default 30 days)
 * - Tier converted datasets to cool then archive (default 30 / 90 days)
 *
 * Each rule is independently togglable via its own should_* flag and uses the
 * -1 sentinel to disable retention (mirrors the platform module convention).
 */

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id

  rule {
    name    = "delete-raw-after-retention"
    enabled = var.should_enable_raw_lifecycle

    filters {
      prefix_match = ["raw/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.raw_retention_days
      }
    }
  }

  rule {
    name    = "tier-converted-cool-then-archive"
    enabled = var.should_enable_converted_lifecycle

    filters {
      prefix_match = ["converted/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = var.converted_cool_days
        tier_to_archive_after_days_since_modification_greater_than = var.converted_archive_days
      }
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
