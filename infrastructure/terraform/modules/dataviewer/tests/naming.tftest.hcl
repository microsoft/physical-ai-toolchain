// Dataviewer module naming convention tests
// Validates resource names follow {abbreviation}-{prefix}-{env}-{instance} convention

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

run "verify_resource_naming" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    network_security_group  = run.setup.network_security_group
    nat_gateway             = run.setup.nat_gateway
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    storage_account         = run.setup.storage_account
  }

  // Container Apps Environment
  assert {
    condition     = azurerm_container_app_environment.main.name == "cae-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Container Apps Environment name must follow cae-{prefix}-{env}-{instance}"
  }

  // Backend Container App
  assert {
    condition     = azurerm_container_app.backend.name == "ca-backend-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Backend Container App name must follow ca-backend-{prefix}-{env}-{instance}"
  }

  // Frontend Container App
  assert {
    condition     = azurerm_container_app.frontend.name == "ca-frontend-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Frontend Container App name must follow ca-frontend-{prefix}-{env}-{instance}"
  }

  // User Assigned Identity
  assert {
    condition     = azurerm_user_assigned_identity.dataviewer.name == "id-dataviewer-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Managed identity name must follow id-dataviewer-{prefix}-{env}-{instance}"
  }

  // Container Apps Subnet
  assert {
    condition     = azurerm_subnet.container_apps.name == "snet-cae-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Container Apps subnet name must follow snet-cae-{prefix}-{env}-{instance}"
  }
}
