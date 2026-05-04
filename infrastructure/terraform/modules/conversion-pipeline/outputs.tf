/**
 * # Module Outputs
 *
 * Typed object outputs consumed by downstream modules (Function App / Fabric
 * pipeline from issues #32, #34, #72) via the variables.deps.tf pattern.
 */

output "event_grid_topic" {
  description = "Event Grid system topic on the platform data-lake account"
  value = {
    id                    = azurerm_eventgrid_system_topic.blob.id
    name                  = azurerm_eventgrid_system_topic.blob.name
    identity_principal_id = azurerm_eventgrid_system_topic.blob.identity[0].principal_id
  }
}

output "event_grid_subscription" {
  description = "Event Grid subscription for raw BlobCreated events"
  value = {
    id   = azurerm_eventgrid_system_topic_event_subscription.raw_blob_created.id
    name = azurerm_eventgrid_system_topic_event_subscription.raw_blob_created.name
  }
}

output "event_grid_dlq_container" {
  description = "Event Grid dead-letter container on the platform data-lake account. Null when DLQ is disabled"
  value = try({
    id   = azurerm_storage_container.event_grid_dlq[0].id
    name = azurerm_storage_container.event_grid_dlq[0].name
  }, null)
}

output "fabric_workspace" {
  description = "Microsoft Fabric workspace bound to the conversion capacity"
  value = try({
    id           = fabric_workspace.this[0].id
    display_name = fabric_workspace.this[0].display_name
  }, null)
}

output "fabric_capacity" {
  description = "Microsoft Fabric capacity. Null when an existing capacity is reused"
  value = try({
    id   = azurerm_fabric_capacity.this[0].id
    name = azurerm_fabric_capacity.this[0].name
    sku  = azurerm_fabric_capacity.this[0].sku[0].name
  }, null)
}
