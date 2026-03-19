// Dataviewer module conditional resource tests
// Validates should_* boolean variables control resource creation correctly

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
// Internal Mode Conditionals
// ============================================================

run "internal_mode_enabled" {
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
    should_enable_internal  = true
  }

  assert {
    condition     = azurerm_container_app_environment.main.internal_load_balancer_enabled == true
    error_message = "Internal load balancer should be enabled in internal mode"
  }

  assert {
    condition     = length(azurerm_private_dns_zone.container_apps) == 1
    error_message = "Private DNS zone should be created in internal mode"
  }

  assert {
    condition     = length(azurerm_private_dns_zone_virtual_network_link.container_apps) == 1
    error_message = "VNet link should be created in internal mode"
  }

  assert {
    condition     = length(azurerm_private_dns_a_record.container_apps_wildcard) == 1
    error_message = "Wildcard A record should be created in internal mode"
  }
}

run "internal_mode_disabled" {
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
    should_enable_internal  = false
  }

  assert {
    condition     = azurerm_container_app_environment.main.internal_load_balancer_enabled == false
    error_message = "Internal load balancer should be disabled in external mode"
  }

  assert {
    condition     = length(azurerm_private_dns_zone.container_apps) == 0
    error_message = "Private DNS zone should not be created in external mode"
  }

  assert {
    condition     = length(azurerm_private_dns_zone_virtual_network_link.container_apps) == 0
    error_message = "VNet link should not be created in external mode"
  }

  assert {
    condition     = length(azurerm_private_dns_a_record.container_apps_wildcard) == 0
    error_message = "Wildcard A record should not be created in external mode"
  }
}

// ============================================================
// Auth Conditionals
// ============================================================

run "auth_enabled" {
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
    condition     = length(azuread_application.dataviewer) == 1
    error_message = "Entra ID app registration should be created when auth is enabled"
  }

  assert {
    condition     = length(azuread_service_principal.dataviewer) == 1
    error_message = "Service principal should be created when auth is enabled"
  }

  assert {
    condition     = length(random_uuid.dataviewer_scope_id) == 1
    error_message = "Random UUID for scope should be created when auth is enabled"
  }
}

run "auth_disabled" {
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
    condition     = length(azuread_application.dataviewer) == 0
    error_message = "Entra ID app registration should not be created when auth is disabled"
  }

  assert {
    condition     = length(azuread_service_principal.dataviewer) == 0
    error_message = "Service principal should not be created when auth is disabled"
  }

  assert {
    condition     = length(random_uuid.dataviewer_scope_id) == 0
    error_message = "Random UUID for scope should not be created when auth is disabled"
  }
}

// ============================================================
// NAT Gateway Conditionals
// ============================================================

run "nat_gateway_associated" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    virtual_network           = run.setup.virtual_network
    network_security_group    = run.setup.network_security_group
    nat_gateway               = run.setup.nat_gateway
    log_analytics_workspace   = run.setup.log_analytics_workspace
    container_registry        = run.setup.container_registry
    storage_account           = run.setup.storage_account
    should_enable_nat_gateway = true
  }

  assert {
    condition     = length(azurerm_subnet_nat_gateway_association.container_apps) == 1
    error_message = "NAT Gateway association should be created when enabled with gateway provided"
  }

  assert {
    condition     = azurerm_subnet.container_apps.default_outbound_access_enabled == false
    error_message = "Default outbound access should be disabled when NAT Gateway is enabled"
  }
}

run "nat_gateway_not_associated" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    virtual_network           = run.setup.virtual_network
    network_security_group    = run.setup.network_security_group
    log_analytics_workspace   = run.setup.log_analytics_workspace
    container_registry        = run.setup.container_registry
    storage_account           = run.setup.storage_account
    should_enable_nat_gateway = false
  }

  assert {
    condition     = length(azurerm_subnet_nat_gateway_association.container_apps) == 0
    error_message = "NAT Gateway association should not be created when disabled"
  }

  assert {
    condition     = azurerm_subnet.container_apps.default_outbound_access_enabled == true
    error_message = "Default outbound access should be enabled when NAT Gateway is disabled"
  }
}
