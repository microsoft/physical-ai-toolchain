/**
 * # Role Assignments and ACLs
 *
 * - Event Grid system topic SystemAssigned MI gets Storage Blob Data Contributor
 *   on the DLQ container so it can write dead-letter blobs.
 * - Fabric workspace service principal gets Storage Blob Data Reader at the
 *   datasets container scope (covers read+list across raw/ and converted/ and
 *   provides RBAC traverse, bypassing the POSIX --x requirement) plus an
 *   ADLS Gen2 ACL granting rwx on converted/ for write access.
 */

resource "azurerm_role_assignment" "eventgrid_dlq_writer" {
  count = var.should_enable_event_grid_dead_letter ? 1 : 0

  scope                = azurerm_storage_container.event_grid_dlq[0].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_eventgrid_system_topic.blob.identity[0].principal_id
}

// Container-scoped read for the Fabric workspace SP across the entire datasets
// container. RBAC data permissions satisfy ADLS Gen2 traverse checks without
// requiring POSIX --x on intermediate directories.
resource "azurerm_role_assignment" "fabric_sp_datasets_reader" {
  count = var.fabric_workspace_sp_object_id == null ? 0 : 1

  scope                = var.datasets_container.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.fabric_workspace_sp_object_id
}

// Write access on datasets/converted/ for the Fabric workspace SP via folder
// ACL (default ACEs propagate the grant to new children).
resource "azurerm_storage_data_lake_gen2_path" "fabric_converted" {
  count = var.fabric_workspace_sp_object_id == null ? 0 : 1

  storage_account_id = var.data_lake_storage_account.id
  filesystem_name    = var.datasets_container.name
  path               = "converted"
  resource           = "directory"

  ace {
    type        = "user"
    id          = var.fabric_workspace_sp_object_id
    scope       = "access"
    permissions = "rwx"
  }

  ace {
    type        = "user"
    id          = var.fabric_workspace_sp_object_id
    scope       = "default"
    permissions = "rwx"
  }

  ace {
    type        = "mask"
    scope       = "access"
    permissions = "rwx"
  }

  ace {
    type        = "mask"
    scope       = "default"
    permissions = "rwx"
  }
}
