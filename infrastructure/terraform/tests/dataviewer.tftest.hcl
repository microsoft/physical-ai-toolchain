// Root dataviewer wiring tests
// Validates that the dataviewer module is opt-in and that the dataviewer output
// exposes the fields deploy-dataviewer.sh reads

mock_provider "azurerm" {
  override_during = plan
}
mock_provider "azuread" {
  override_during = plan
}
mock_provider "azapi" {
  override_during = plan
}
mock_provider "msgraph" {
  override_during = plan
}
mock_provider "tls" {
  override_during = plan
}
mock_provider "random" {
  override_during = plan
}

override_data {
  target = module.platform.data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

variables {
  aml_managed_network_isolation_mode = "Disabled"
  should_create_resource_group       = true
  should_deploy_aks                  = false
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Disabled by Default
// ============================================================

run "dataviewer_disabled_by_default" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
  }

  assert {
    condition     = length(module.dataviewer) == 0
    error_message = "Dataviewer module should not be instantiated unless should_deploy_dataviewer is true"
  }

  assert {
    condition     = output.dataviewer == null
    error_message = "dataviewer output should be null when the dataviewer is not deployed"
  }
}

// ============================================================
// Enabled
// ============================================================

run "dataviewer_enabled" {
  command = plan

  override_resource {
    target          = module.dataviewer[0].azurerm_user_assigned_identity.dataviewer
    override_during = plan
    values = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dataviewer-test"
      principal_id = "00000000-0000-0000-0000-000000000011"
      client_id    = "00000000-0000-0000-0000-000000000012"
    }
  }

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    should_deploy_dataviewer = true
  }

  assert {
    condition     = length(module.dataviewer) == 1
    error_message = "Dataviewer module should be instantiated when should_deploy_dataviewer is true"
  }

  assert {
    condition     = length(keys(output.dataviewer)) == 5 && alltrue([for key in ["environment", "backend", "frontend", "identity", "entra_id"] : contains(keys(output.dataviewer), key)])
    error_message = "dataviewer output should expose environment, backend, frontend, identity, and entra_id"
  }

  assert {
    condition     = output.dataviewer.backend.name == module.dataviewer[0].backend.name && output.dataviewer.frontend.name == module.dataviewer[0].frontend.name
    error_message = "dataviewer output should pass through the module's container app names"
  }

  assert {
    condition     = output.dataviewer.identity.id == "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dataviewer-test"
    error_message = "dataviewer identity should come from the module's managed identity"
  }

  assert {
    condition     = output.dataviewer.environment.name == module.dataviewer[0].container_app_environment.name
    error_message = "dataviewer environment should come from the module's Container Apps environment"
  }

  assert {
    condition     = output.dataviewer.entra_id == null
    error_message = "dataviewer entra_id should be null when auth is not deployed"
  }
}

// ============================================================
// Auth Pass-Through
// ============================================================

run "dataviewer_auth_enabled" {
  command = plan

  override_resource {
    target          = module.dataviewer[0].azuread_application.dataviewer[0]
    override_during = plan
    values = {
      client_id = "00000000-0000-0000-0000-000000000021"
    }
  }

  // tenant_id comes from a data source that the module's depends_on defers to apply, so it isn't asserted here

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    should_deploy_dataviewer = true
    dataviewer_config = {
      should_deploy_auth = true
    }
  }

  assert {
    condition     = output.dataviewer.entra_id.client_id == "00000000-0000-0000-0000-000000000021"
    error_message = "dataviewer entra_id should expose the app registration client ID when dataviewer_config.should_deploy_auth is true"
  }
}
