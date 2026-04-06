// Dataviewer module container apps tests
// Validates container image selection and ACR registry configuration

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
// Container Image Selection
// ============================================================

run "default_placeholder_images" {
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
    backend_image           = ""
    frontend_image          = ""
  }

  assert {
    condition     = azurerm_container_app.backend.template[0].container[0].image == "mcr.microsoft.com/k8se/quickstart:latest"
    error_message = "Backend should use placeholder image when no custom image is specified"
  }

  assert {
    condition     = azurerm_container_app.frontend.template[0].container[0].image == "mcr.microsoft.com/k8se/quickstart:latest"
    error_message = "Frontend should use placeholder image when no custom image is specified"
  }
}

run "custom_images" {
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
    backend_image           = "acrtestdev001.azurecr.io/dataviewer-backend:v1.0"
    frontend_image          = "acrtestdev001.azurecr.io/dataviewer-frontend:v1.0"
  }

  assert {
    condition     = azurerm_container_app.backend.template[0].container[0].image == "acrtestdev001.azurecr.io/dataviewer-backend:v1.0"
    error_message = "Backend should use custom image when specified"
  }

  assert {
    condition     = azurerm_container_app.frontend.template[0].container[0].image == "acrtestdev001.azurecr.io/dataviewer-frontend:v1.0"
    error_message = "Frontend should use custom image when specified"
  }
}

// ============================================================
// ACR Registry Configuration
// ============================================================

run "acr_registry_enabled" {
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
    condition     = length(azurerm_container_app.backend.registry) == 1
    error_message = "Backend should have ACR registry block when login_server is configured"
  }

  assert {
    condition     = length(azurerm_container_app.frontend.registry) == 1
    error_message = "Frontend should have ACR registry block when login_server is configured"
  }
}

run "acr_registry_disabled" {
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
    container_registry = {
      id           = run.setup.container_registry.id
      name         = run.setup.container_registry.name
      login_server = ""
    }
    storage_account = run.setup.storage_account
  }

  assert {
    condition     = length(azurerm_container_app.backend.registry) == 0
    error_message = "Backend should not have ACR registry block when login_server is empty"
  }

  assert {
    condition     = length(azurerm_container_app.frontend.registry) == 0
    error_message = "Frontend should not have ACR registry block when login_server is empty"
  }
}
