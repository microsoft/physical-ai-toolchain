/**
 * # Module Outputs
 *
 * Typed object outputs consumed by downstream modules (Function App / Fabric
 * pipeline from issues #32, #34, #72) via the variables.deps.tf pattern.
 */

output "storage_account" {
  description = "Conversion-pipeline storage account"
  value = {
    id                    = azurerm_storage_account.this.id
    name                  = azurerm_storage_account.this.name
    primary_blob_endpoint = azurerm_storage_account.this.primary_blob_endpoint
    primary_dfs_endpoint  = azurerm_storage_account.this.primary_dfs_endpoint
  }
}

output "raw_container" {
  description = "Raw blob container"
  value = {
    id   = azurerm_storage_container.raw.id
    name = azurerm_storage_container.raw.name
  }
}

output "converted_container" {
  description = "Converted blob container"
  value = {
    id   = azurerm_storage_container.converted.id
    name = azurerm_storage_container.converted.name
  }
}

output "event_grid_topic" {
  description = "Event Grid system topic on the conversion storage account"
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

output "fabric_workspace" {
  description = "Microsoft Fabric workspace bound to the conversion capacity. Null when workspace creation is deferred (see README two-pass deployment)"
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
