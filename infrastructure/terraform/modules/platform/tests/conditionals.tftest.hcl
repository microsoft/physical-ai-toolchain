// Platform module conditional resource tests
// Validates should_* boolean variables control resource creation correctly

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "random" {}

override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

variables {
  current_user_oid = "00000000-0000-0000-0000-000000000001"
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// NAT Gateway Conditionals
// ============================================================

run "nat_gateway_enabled" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    current_user_oid          = run.setup.current_user_oid
    should_enable_nat_gateway = true
  }

  assert {
    condition     = length(azurerm_nat_gateway.main) == 1
    error_message = "NAT Gateway should be created when enabled"
  }

  assert {
    condition     = length(azurerm_public_ip.nat_gateway) == 1
    error_message = "NAT Gateway public IP should be created when enabled"
  }

  assert {
    condition     = azurerm_subnet.main.default_outbound_access_enabled == false
    error_message = "Main subnet outbound access should be disabled when NAT Gateway is enabled"
  }
}

run "nat_gateway_disabled" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    current_user_oid          = run.setup.current_user_oid
    should_enable_nat_gateway = false
  }

  assert {
    condition     = length(azurerm_nat_gateway.main) == 0
    error_message = "NAT Gateway should not be created when disabled"
  }

  assert {
    condition     = azurerm_subnet.main.default_outbound_access_enabled == true
    error_message = "Main subnet outbound access should be enabled when NAT Gateway is disabled"
  }
}

// ============================================================
// Private Endpoint Conditionals
// ============================================================

run "private_endpoints_enabled" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_subnet.private_endpoints) == 1
    error_message = "PE subnet should be created when PE is enabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.acr) == 1
    error_message = "ACR PE should be created when PE is enabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.key_vault) == 1
    error_message = "Key Vault PE should be created when PE is enabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.storage_blob) == 1
    error_message = "Storage blob PE should be created when PE is enabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.storage_file) == 1
    error_message = "Storage file PE should be created when PE is enabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.azureml_api) == 1
    error_message = "ML API PE should be created when PE is enabled"
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
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(azurerm_subnet.private_endpoints) == 0
    error_message = "PE subnet should not be created when PE is disabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.acr) == 0
    error_message = "ACR PE should not exist when PE is disabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.key_vault) == 0
    error_message = "Key Vault PE should not exist when PE is disabled"
  }
}

// ============================================================
// Grafana Conditional
// ============================================================

run "grafana_enabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    current_user_oid      = run.setup.current_user_oid
    should_deploy_grafana = true
  }

  assert {
    condition     = length(azurerm_dashboard_grafana.main) == 1
    error_message = "Grafana should be created when enabled"
  }
}

run "grafana_disabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    current_user_oid      = run.setup.current_user_oid
    should_deploy_grafana = false
  }

  assert {
    condition     = length(azurerm_dashboard_grafana.main) == 0
    error_message = "Grafana should not be created when disabled"
  }
}

// ============================================================
// Monitor Workspace Conditional
// ============================================================

run "monitor_workspace_enabled" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    current_user_oid                = run.setup.current_user_oid
    should_deploy_monitor_workspace = true
  }

  assert {
    condition     = length(azurerm_monitor_workspace.main) == 1
    error_message = "Monitor workspace should be created when enabled"
  }
}

run "monitor_workspace_disabled" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    current_user_oid                = run.setup.current_user_oid
    should_deploy_monitor_workspace = false
  }

  assert {
    condition     = length(azurerm_monitor_workspace.main) == 0
    error_message = "Monitor workspace should not be created when disabled"
  }
}

// ============================================================
// DCE Conditional
// ============================================================

run "dce_enabled" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    should_deploy_dce = true
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_endpoint.main) == 1
    error_message = "DCE should be created when enabled"
  }
}

run "dce_disabled" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    should_deploy_dce = false
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_endpoint.main) == 0
    error_message = "DCE should not be created when disabled"
  }
}

// ============================================================
// AMPLS Conditional (requires PE to be on)
// ============================================================

run "ampls_with_pe" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_ampls            = true
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_monitor_private_link_scope.main) == 1
    error_message = "AMPLS should be created when both AMPLS and PE are enabled"
  }
}

run "ampls_without_pe" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_ampls            = true
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(azurerm_monitor_private_link_scope.main) == 0
    error_message = "AMPLS should not be created when PE is disabled even if AMPLS flag is true"
  }
}

// AMPLS + DCE scoped service link
run "ampls_dce_pe_triple" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_ampls            = true
    should_deploy_dce              = true
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_monitor_private_link_scoped_service.dce) == 1
    error_message = "DCE scoped service link should be created when AMPLS, DCE, and PE are all enabled"
  }
}

run "ampls_dce_pe_missing_dce" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_ampls            = true
    should_deploy_dce              = false
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_monitor_private_link_scoped_service.dce) == 0
    error_message = "DCE scoped service link should not exist when DCE is disabled"
  }
}

// ============================================================
// PostgreSQL Conditional
// ============================================================

run "postgresql_enabled" {
  command = plan

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    resource_group           = run.setup.resource_group
    current_user_oid         = run.setup.current_user_oid
    should_deploy_postgresql = true
  }

  assert {
    condition     = length(azurerm_postgresql_flexible_server.main) == 1
    error_message = "PostgreSQL should be created when enabled"
  }

  assert {
    condition     = length(random_password.postgresql) == 1
    error_message = "PostgreSQL random password should be created when PostgreSQL is enabled"
  }
}

run "postgresql_disabled" {
  command = plan

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    resource_group           = run.setup.resource_group
    current_user_oid         = run.setup.current_user_oid
    should_deploy_postgresql = false
  }

  assert {
    condition     = length(azurerm_postgresql_flexible_server.main) == 0
    error_message = "PostgreSQL should not be created when disabled"
  }
}

run "postgresql_pe" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_postgresql       = true
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_private_endpoint.postgresql) == 1
    error_message = "PostgreSQL PE should be created when both PostgreSQL and PE are enabled"
  }
}

run "postgresql_no_pe" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_postgresql       = true
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(azurerm_private_endpoint.postgresql) == 0
    error_message = "PostgreSQL PE should not exist when PE is disabled"
  }
}

// ============================================================
// Redis Conditional
// ============================================================

run "redis_enabled" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    current_user_oid    = run.setup.current_user_oid
    should_deploy_redis = true
  }

  assert {
    condition     = length(azurerm_managed_redis.main) == 1
    error_message = "Redis should be created when enabled"
  }
}

run "redis_disabled" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    current_user_oid    = run.setup.current_user_oid
    should_deploy_redis = false
  }

  assert {
    condition     = length(azurerm_managed_redis.main) == 0
    error_message = "Redis should not be created when disabled"
  }
}

run "redis_pe_dns" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_redis            = true
    should_enable_private_endpoint = true
  }

  assert {
    condition     = length(azurerm_private_dns_zone.redis) == 1
    error_message = "Redis DNS zone should be created when Redis and PE are enabled"
  }

  assert {
    condition     = length(azurerm_private_endpoint.redis) == 1
    error_message = "Redis PE should be created when Redis and PE are enabled"
  }
}

run "redis_no_pe" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_deploy_redis            = true
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(azurerm_private_dns_zone.redis) == 0
    error_message = "Redis DNS zone should not exist when PE is disabled"
  }
}

// ============================================================
// OSMO Identity Conditional
// ============================================================

run "osmo_identity_enabled" {
  command = plan

  variables {
    resource_prefix             = run.setup.resource_prefix
    environment                 = run.setup.environment
    instance                    = run.setup.instance
    location                    = run.setup.location
    resource_group              = run.setup.resource_group
    current_user_oid            = run.setup.current_user_oid
    should_enable_osmo_identity = true
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.osmo) == 1
    error_message = "OSMO identity should be created when enabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.osmo_storage_blob_contributor) == 1
    error_message = "OSMO storage blob role assignment should be created when OSMO identity is enabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.osmo_acr_pull) == 1
    error_message = "OSMO ACR pull role assignment should be created when OSMO identity is enabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.osmo_kv_secrets_user) == 1
    error_message = "OSMO KV secrets user role assignment should be created when OSMO identity is enabled"
  }
}

run "osmo_identity_disabled" {
  command = plan

  variables {
    resource_prefix             = run.setup.resource_prefix
    environment                 = run.setup.environment
    instance                    = run.setup.instance
    location                    = run.setup.location
    resource_group              = run.setup.resource_group
    current_user_oid            = run.setup.current_user_oid
    should_enable_osmo_identity = false
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.osmo) == 0
    error_message = "OSMO identity should not be created when disabled"
  }
}

// ============================================================
// AML Diagnostic Logs Conditional
// ============================================================

run "aml_diagnostic_logs_enabled" {
  command = plan

  variables {
    resource_prefix                   = run.setup.resource_prefix
    environment                       = run.setup.environment
    instance                          = run.setup.instance
    location                          = run.setup.location
    resource_group                    = run.setup.resource_group
    current_user_oid                  = run.setup.current_user_oid
    should_enable_aml_diagnostic_logs = true
  }

  assert {
    condition     = length(azurerm_monitor_diagnostic_setting.ml_workspace_logs) == 1
    error_message = "AML diagnostic setting should be created when enabled"
  }

  assert {
    condition     = azurerm_monitor_diagnostic_setting.ml_workspace_logs[0].name == "diag-mlw-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "AML diagnostic setting should use the standard diagnostic setting name"
  }

  assert {
    condition     = one(azurerm_monitor_diagnostic_setting.ml_workspace_logs[0].enabled_log).category_group == "allLogs"
    error_message = "AML diagnostic setting should enable all AML log categories"
  }
}

run "aml_diagnostic_logs_disabled" {
  command = plan

  variables {
    resource_prefix                   = run.setup.resource_prefix
    environment                       = run.setup.environment
    instance                          = run.setup.instance
    location                          = run.setup.location
    resource_group                    = run.setup.resource_group
    current_user_oid                  = run.setup.current_user_oid
    should_enable_aml_diagnostic_logs = false
  }

  assert {
    condition     = length(azurerm_monitor_diagnostic_setting.ml_workspace_logs) == 0
    error_message = "AML diagnostic setting should not be created when disabled"
  }
}

// ============================================================
// AML Compute Conditional
// ============================================================

run "aml_compute_enabled" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    current_user_oid          = run.setup.current_user_oid
    should_deploy_aml_compute = true
  }

  assert {
    condition     = length(azurerm_machine_learning_compute_cluster.gpu) == 1
    error_message = "AML compute cluster should be created when enabled"
  }
}

run "aml_compute_disabled" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    current_user_oid          = run.setup.current_user_oid
    should_deploy_aml_compute = false
  }

  assert {
    condition     = length(azurerm_machine_learning_compute_cluster.gpu) == 0
    error_message = "AML compute cluster should not be created when disabled"
  }
}

// ============================================================
// Data Lake Storage Conditionals
// ============================================================

run "data_lake_enabled" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    current_user_oid                = run.setup.current_user_oid
    should_create_data_lake_storage = true
  }

  assert {
    condition     = length(azurerm_storage_account.data_lake) == 1
    error_message = "Data lake storage account should be created when enabled"
  }

  assert {
    condition     = length(azurerm_storage_container.datasets) == 1
    error_message = "Datasets container should be created when data lake is enabled"
  }

  assert {
    condition     = length(azurerm_storage_container.models) == 1
    error_message = "Models container should be created when data lake is enabled"
  }

  assert {
    condition     = length(azurerm_storage_container.evaluation) == 1
    error_message = "Evaluation container should be created when data lake is enabled"
  }

  assert {
    condition     = length(azurerm_storage_management_policy.data_lake) == 1
    error_message = "Data lake lifecycle policy should be created when data lake is enabled"
  }
}

run "data_lake_disabled" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    current_user_oid                = run.setup.current_user_oid
    should_create_data_lake_storage = false
  }

  assert {
    condition     = length(azurerm_storage_account.data_lake) == 0
    error_message = "Data lake storage account should not be created when disabled"
  }

  assert {
    condition     = length(azurerm_storage_container.datasets) == 0
    error_message = "Datasets container should not exist when data lake is disabled"
  }

  assert {
    condition     = length(azurerm_storage_container.models) == 0
    error_message = "Models container should not exist when data lake is disabled"
  }

  assert {
    condition     = length(azurerm_storage_container.evaluation) == 0
    error_message = "Evaluation container should not exist when data lake is disabled"
  }
}
