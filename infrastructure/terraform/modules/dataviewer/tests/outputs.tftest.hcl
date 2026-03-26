// Dataviewer module output structure tests
// Validates output contracts (presence and nullability)

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "random" {}

override_data {
  target = data.azuread_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
    object_id = "00000000-0000-0000-0000-000000000001"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Entra ID Output Nullability
// ============================================================

run "entra_id_null_when_auth_disabled" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    network_security_group        = run.setup.network_security_group
    log_analytics_workspace       = run.setup.log_analytics_workspace
    container_registry            = run.setup.container_registry
    storage_account               = run.setup.storage_account
    should_deploy_dataviewer_auth = false
  }

  assert {
    condition     = output.entra_id == null
    error_message = "entra_id output should be null when auth is disabled"
  }
}

run "entra_id_present_when_auth_enabled" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    network_security_group        = run.setup.network_security_group
    log_analytics_workspace       = run.setup.log_analytics_workspace
    container_registry            = run.setup.container_registry
    storage_account               = run.setup.storage_account
    should_deploy_dataviewer_auth = true
  }

  assert {
    condition     = output.entra_id != null
    error_message = "entra_id output should be populated when auth is enabled"
  }
}

// ============================================================
// Core Output Structure
// ============================================================

run "core_outputs_present" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    network_security_group  = run.setup.network_security_group
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    storage_account         = run.setup.storage_account
  }

  assert {
    condition     = output.container_app_environment != null
    error_message = "container_app_environment output should always be present"
  }

  assert {
    condition     = output.dataviewer_identity != null
    error_message = "dataviewer_identity output should always be present"
  }
}
