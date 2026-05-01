// Conversion pipeline module tests
// All runs use command = plan against mock providers; no Azure credentials required.
//
// The root-level precondition coupling should_deploy_conversion_pipeline to
// should_create_data_lake_storage lives on a terraform_data resource at root
// (DD-03) and cannot be exercised from within this module-scoped test file.

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
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    data_lake_storage_account = run.setup.data_lake_storage_account
    datasets_container        = run.setup.datasets_container
  }

  assert {
    condition     = azurerm_eventgrid_system_topic.blob.name == "evgt-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Event Grid system topic name must follow evgt-{suffix} convention."
  }

  assert {
    condition     = azurerm_eventgrid_system_topic.blob.source_resource_id == run.setup.data_lake_storage_account.id
    error_message = "Event Grid system topic must be parented to the platform data-lake account."
  }
}

// ============================================================
// DLQ Container
// ============================================================

run "dlq_container_created" {
  command = plan

  variables {
    resource_prefix                      = run.setup.resource_prefix
    environment                          = run.setup.environment
    instance                             = run.setup.instance
    location                             = run.setup.location
    resource_group                       = run.setup.resource_group
    data_lake_storage_account            = run.setup.data_lake_storage_account
    datasets_container                   = run.setup.datasets_container
    should_enable_event_grid_dead_letter = true
  }

  assert {
    condition     = length(azurerm_storage_container.event_grid_dlq) == 1
    error_message = "Dead-letter container must be created when should_enable_event_grid_dead_letter is true."
  }

  assert {
    condition     = azurerm_storage_container.event_grid_dlq[0].storage_account_id == run.setup.data_lake_storage_account.id
    error_message = "Dead-letter container must be parented to the platform data-lake account."
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
    data_lake_storage_account            = run.setup.data_lake_storage_account
    datasets_container                   = run.setup.datasets_container
    should_enable_event_grid_dead_letter = false
  }

  assert {
    condition     = length(azurerm_storage_container.event_grid_dlq) == 0
    error_message = "Dead-letter container must not be created when DLQ is disabled."
  }
}

// ============================================================
// Event Grid Filters
// ============================================================

run "event_grid_filters" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    data_lake_storage_account = run.setup.data_lake_storage_account
    datasets_container        = run.setup.datasets_container
    raw_blob_suffix_filters   = [".bag", ".bag.zst", ".mcap"]
  }

  assert {
    condition     = contains(azurerm_eventgrid_system_topic_event_subscription.raw_blob_created.advanced_filter[0].string_ends_with[0].values, ".bag.zst")
    error_message = "Event Grid subscription must filter on .bag.zst suffix."
  }

  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.raw_blob_created.subject_filter[0].subject_begins_with == "/blobServices/default/containers/datasets/blobs/raw/"
    error_message = "Event Grid subscription must use the ADLS Gen2 HNS subject prefix for the platform datasets/raw/ path."
  }
}

// ============================================================
// Fabric Capacity / Workspace
// ============================================================

run "fabric_capacity_created" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    data_lake_storage_account      = run.setup.data_lake_storage_account
    datasets_container             = run.setup.datasets_container
    should_create_fabric_capacity  = true
    should_create_fabric_workspace = true
    fabric_capacity_sku            = "F2"
  }

  assert {
    condition     = length(azurerm_fabric_capacity.this) == 1
    error_message = "Fabric capacity must be created when should_create_fabric_capacity is true."
  }

  assert {
    condition     = length(fabric_workspace.this) == 1
    error_message = "Fabric workspace must be created when should_create_fabric_workspace is true."
  }

  assert {
    condition     = length(data.fabric_capacity.created) == 1
    error_message = "Deferred data.fabric_capacity.created lookup must resolve when capacity creation is enabled."
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
    data_lake_storage_account      = run.setup.data_lake_storage_account
    datasets_container             = run.setup.datasets_container
    should_create_fabric_capacity  = false
    should_create_fabric_workspace = true
  }

  assert {
    condition     = length(azurerm_fabric_capacity.this) == 0
    error_message = "Fabric capacity must not be created when reusing an existing capacity."
  }
}

// ============================================================
// Fabric SP Permissions
// ============================================================

run "fabric_sp_permissions_created" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    data_lake_storage_account     = run.setup.data_lake_storage_account
    datasets_container            = run.setup.datasets_container
    fabric_workspace_sp_object_id = "00000000-0000-0000-0000-000000000099"
  }

  assert {
    condition     = length(azurerm_role_assignment.fabric_sp_datasets_reader) == 1
    error_message = "Fabric SP datasets Reader role assignment must be created when fabric_workspace_sp_object_id is set."
  }

  assert {
    condition     = azurerm_role_assignment.fabric_sp_datasets_reader[0].role_definition_name == "Storage Blob Data Reader"
    error_message = "Fabric SP must be granted Storage Blob Data Reader at container scope."
  }

  assert {
    condition     = azurerm_role_assignment.fabric_sp_datasets_reader[0].scope == run.setup.datasets_container.id
    error_message = "Fabric SP Reader role must be scoped to the datasets container."
  }

  assert {
    condition     = length(azurerm_storage_data_lake_gen2_path.fabric_converted) == 1
    error_message = "Fabric SP converted/ ACL path must be created when fabric_workspace_sp_object_id is set."
  }

  assert {
    condition     = azurerm_storage_data_lake_gen2_path.fabric_converted[0].path == "converted"
    error_message = "Fabric SP converted/ ACL must target the converted directory."
  }
}

run "fabric_sp_permissions_skipped_when_unset" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    data_lake_storage_account = run.setup.data_lake_storage_account
    datasets_container        = run.setup.datasets_container
  }

  assert {
    condition     = length(azurerm_role_assignment.fabric_sp_datasets_reader) == 0
    error_message = "Fabric SP Reader role must not be created when fabric_workspace_sp_object_id is null."
  }

  assert {
    condition     = length(azurerm_storage_data_lake_gen2_path.fabric_converted) == 0
    error_message = "Fabric SP converted/ ACL must not be created when fabric_workspace_sp_object_id is null."
  }
}

// ============================================================
// Variable Validation
// ============================================================

run "invalid_sku_rejected" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    data_lake_storage_account = run.setup.data_lake_storage_account
    datasets_container        = run.setup.datasets_container
    fabric_capacity_sku       = "F1"
  }

  expect_failures = [var.fabric_capacity_sku]
}

run "empty_suffix_filters_rejected" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    data_lake_storage_account = run.setup.data_lake_storage_account
    datasets_container        = run.setup.datasets_container
    raw_blob_suffix_filters   = []
  }

  expect_failures = [var.raw_blob_suffix_filters]
}
