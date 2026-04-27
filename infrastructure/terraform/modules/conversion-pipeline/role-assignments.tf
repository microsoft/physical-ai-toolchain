/**
 * # Role Assignments
 *
 * - Event Grid system topic SystemAssigned MI gets Storage Blob Data Contributor
 *   on the conversion storage account so it can write dead-letter blobs.
 * - Fabric workspace service principal gets Storage Blob Data Reader on raw
 *   and Storage Blob Data Contributor on converted (only when supplied).
 */

resource "azurerm_role_assignment" "eventgrid_dlq_writer" {
  count = var.should_enable_event_grid_dead_letter ? 1 : 0

  scope                = azurerm_storage_container.event_grid_dlq[0].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_eventgrid_system_topic.blob.identity[0].principal_id
}

resource "azurerm_role_assignment" "fabric_sp_raw_reader" {
  count = var.fabric_workspace_sp_object_id == null ? 0 : 1

  scope                = azurerm_storage_container.raw.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = var.fabric_workspace_sp_object_id
}

resource "azurerm_role_assignment" "fabric_sp_converted_contributor" {
  count = var.fabric_workspace_sp_object_id == null ? 0 : 1

  scope                = azurerm_storage_container.converted.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.fabric_workspace_sp_object_id
}
