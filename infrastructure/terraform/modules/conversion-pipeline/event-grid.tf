/**
 * # Event Grid System Topic and Subscription
 *
 * System topic on the conversion storage account that fires BlobCreated events
 * to the conversion subscriber (Function App or Fabric pipeline). Multi-suffix
 * filtering uses advanced_filter.string_ends_with because subject_filter only
 * supports a single suffix value.
 */

resource "azurerm_eventgrid_system_topic" "blob" {
  name                = "evgt-${local.resource_name_suffix}"
  location            = local.location
  resource_group_name = var.resource_group.name
  source_resource_id  = azurerm_storage_account.this.id
  topic_type          = "Microsoft.Storage.StorageAccounts"

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_eventgrid_system_topic_event_subscription" "raw_blob_created" {
  name                = "evgs-raw-${local.resource_name_suffix}"
  system_topic        = azurerm_eventgrid_system_topic.blob.name
  resource_group_name = var.resource_group.name

  included_event_types = ["Microsoft.Storage.BlobCreated"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/raw/"
  }

  advanced_filter {
    string_ends_with {
      key    = "subject"
      values = var.raw_blob_suffix_filters
    }
  }

  retry_policy {
    max_delivery_attempts = 5
    event_time_to_live    = 1440
  }

  dynamic "webhook_endpoint" {
    for_each = var.conversion_subscriber_url == null ? [] : [1]
    content {
      url = var.conversion_subscriber_url
    }
  }

  dynamic "storage_blob_dead_letter_destination" {
    for_each = var.should_enable_event_grid_dead_letter ? [1] : []
    content {
      storage_account_id          = azurerm_storage_account.this.id
      storage_blob_container_name = azurerm_storage_container.event_grid_dlq[0].name
    }
  }

  dynamic "dead_letter_identity" {
    for_each = var.should_enable_event_grid_dead_letter ? [1] : []
    content {
      type = "SystemAssigned"
    }
  }

  delivery_identity {
    type = "SystemAssigned"
  }
}
