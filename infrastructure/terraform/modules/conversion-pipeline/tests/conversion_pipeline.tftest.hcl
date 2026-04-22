// Conversion pipeline module tests
// All runs use command = plan against mock providers; no Azure credentials required.

mock_provider "azurerm" {}
mock_provider "fabric" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Default Naming
// ============================================================

run "default_naming" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    private_dns_zones       = run.setup.private_dns_zones
    log_analytics_workspace = run.setup.log_analytics_workspace
  }

  assert {
    condition     = azurerm_storage_account.this.name == "stcp${run.setup.resource_prefix}${run.setup.environment}${run.setup.instance}"
    error_message = "Storage account name must follow stcp{prefix}{env}{instance} convention."
  }

  assert {
    condition     = azurerm_storage_account.this.is_hns_enabled == true
    error_message = "Storage account must enable hierarchical namespace for ADLS Gen2 / Fabric OneLake."
  }

  assert {
    condition     = azurerm_eventgrid_system_topic.blob.name == "evgt-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Event Grid system topic name must follow evgt-{suffix} convention."
  }
}

// ============================================================
// Containers Created
// ============================================================

run "containers_created" {
  command = plan

  variables {
    resource_prefix                      = run.setup.resource_prefix
    environment                          = run.setup.environment
    instance                             = run.setup.instance
    location                             = run.setup.location
    resource_group                       = run.setup.resource_group
    virtual_network                      = run.setup.virtual_network
    subnets                              = run.setup.subnets
    private_dns_zones                    = run.setup.private_dns_zones
    log_analytics_workspace              = run.setup.log_analytics_workspace
    should_enable_event_grid_dead_letter = true
  }

  assert {
    condition     = azurerm_storage_container.raw.name == "raw"
    error_message = "Raw container must be named 'raw'."
  }

  assert {
    condition     = azurerm_storage_container.converted.name == "converted"
    error_message = "Converted container must be named 'converted'."
  }

  assert {
    condition     = length(azurerm_storage_container.event_grid_dlq) == 1
    error_message = "Dead-letter container must be created when should_enable_event_grid_dead_letter is true."
  }
}

run "dlq_disabled_skips_container" {
  command = plan

  variables {
    resource_prefix                      = run.setup.resource_prefix
    environment                          = run.setup.environment
    instance                             = run.setup.instance
    location                             = run.setup.location
    resource_group                       = run.setup.resource_group
    virtual_network                      = run.setup.virtual_network
    subnets                              = run.setup.subnets
    private_dns_zones                    = run.setup.private_dns_zones
    log_analytics_workspace              = run.setup.log_analytics_workspace
    should_enable_event_grid_dead_letter = false
  }

  assert {
    condition     = length(azurerm_storage_container.event_grid_dlq) == 0
    error_message = "Dead-letter container must not be created when DLQ is disabled."
  }
}

// ============================================================
// Lifecycle Defaults
// ============================================================

run "lifecycle_defaults" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    private_dns_zones       = run.setup.private_dns_zones
    log_analytics_workspace = run.setup.log_analytics_workspace
  }

  assert {
    condition     = azurerm_storage_management_policy.this.rule[0].actions[0].base_blob[0].delete_after_days_since_modification_greater_than == 30
    error_message = "Default raw retention must be 30 days."
  }

  assert {
    condition     = azurerm_storage_management_policy.this.rule[1].actions[0].base_blob[0].tier_to_archive_after_days_since_modification_greater_than == 90
    error_message = "Default converted archive tier must be 90 days."
  }
}

// ============================================================
// Event Grid Filters
// ============================================================

run "event_grid_filters" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    private_dns_zones       = run.setup.private_dns_zones
    log_analytics_workspace = run.setup.log_analytics_workspace
    raw_blob_suffix_filters = [".bag", ".bag.zst", ".mcap"]
  }

  assert {
    condition     = contains(azurerm_eventgrid_system_topic_event_subscription.raw_blob_created.advanced_filter[0].string_ends_with[0].values, ".bag.zst")
    error_message = "Event Grid subscription must filter on .bag.zst suffix."
  }

  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.raw_blob_created.subject_filter[0].subject_begins_with == "/blobServices/default/containers/raw/"
    error_message = "Event Grid subscription must filter to the raw container subject prefix."
  }
}

// ============================================================
// Private Endpoints
// ============================================================

run "private_endpoints_enabled" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    virtual_network                = run.setup.virtual_network
    subnets                        = run.setup.subnets
    private_dns_zones              = run.setup.private_dns_zones
    log_analytics_workspace        = run.setup.log_analytics_workspace
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_private_endpoint.blob) == 1 && length(azurerm_private_endpoint.dfs) == 1
    error_message = "Both blob and dfs private endpoints must be created when should_enable_private_endpoint is true."
  }
}

run "private_endpoints_disabled" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    virtual_network                = run.setup.virtual_network
    subnets                        = run.setup.subnets
    private_dns_zones              = run.setup.private_dns_zones
    log_analytics_workspace        = run.setup.log_analytics_workspace
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(azurerm_private_endpoint.blob) == 0 && length(azurerm_private_endpoint.dfs) == 0
    error_message = "Private endpoints must not be created when should_enable_private_endpoint is false."
  }
}

// ============================================================
// Fabric Capacity Optional
// ============================================================

run "fabric_capacity_created" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    subnets                       = run.setup.subnets
    private_dns_zones             = run.setup.private_dns_zones
    log_analytics_workspace       = run.setup.log_analytics_workspace
    should_create_fabric_capacity = true
    fabric_capacity_sku           = "F2"
  }

  assert {
    condition     = length(azurerm_fabric_capacity.this) == 1
    error_message = "Fabric capacity must be created when should_create_fabric_capacity is true."
  }
}

run "fabric_capacity_reused" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    virtual_network                = run.setup.virtual_network
    subnets                        = run.setup.subnets
    private_dns_zones              = run.setup.private_dns_zones
    log_analytics_workspace        = run.setup.log_analytics_workspace
    should_create_fabric_capacity  = false
    should_create_fabric_workspace = true
    fabric_capacity_uuid           = "11111111-1111-1111-1111-111111111111"
  }

  assert {
    condition     = length(azurerm_fabric_capacity.this) == 0
    error_message = "Fabric capacity must not be created when reusing an existing capacity."
  }
}

// ============================================================
// Variable Validation
// ============================================================

run "invalid_sku_rejected" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    private_dns_zones       = run.setup.private_dns_zones
    log_analytics_workspace = run.setup.log_analytics_workspace
    fabric_capacity_sku     = "F1"
  }

  expect_failures = [var.fabric_capacity_sku]
}

run "invalid_replication_rejected" {
  command = plan

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    resource_group           = run.setup.resource_group
    virtual_network          = run.setup.virtual_network
    subnets                  = run.setup.subnets
    private_dns_zones        = run.setup.private_dns_zones
    log_analytics_workspace  = run.setup.log_analytics_workspace
    storage_replication_type = "PRS"
  }

  expect_failures = [var.storage_replication_type]
}

run "empty_suffix_filters_rejected" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    private_dns_zones       = run.setup.private_dns_zones
    log_analytics_workspace = run.setup.log_analytics_workspace
    raw_blob_suffix_filters = []
  }

  expect_failures = [var.raw_blob_suffix_filters]
}
