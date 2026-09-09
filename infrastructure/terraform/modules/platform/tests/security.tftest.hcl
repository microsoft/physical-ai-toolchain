// Platform module security configuration tests
// Validates Key Vault, Storage, and ACR security settings
// Uses command = plan and checks input-derived attributes only (not computed attributes)

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
  current_user_oid                   = "00000000-0000-0000-0000-000000000001"
  aml_managed_network_isolation_mode = "Disabled"
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "kv_purge_protection_default" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  assert {
    condition     = azurerm_key_vault.main.purge_protection_enabled == false
    error_message = "Key Vault purge protection should be disabled by default"
  }

  assert {
    condition     = azurerm_key_vault.main.rbac_authorization_enabled == true
    error_message = "Key Vault must use RBAC authorization"
  }

  assert {
    condition     = azurerm_key_vault.main.sku_name == "standard"
    error_message = "Key Vault SKU should be standard"
  }
}

run "kv_purge_protection_enabled" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_purge_protection = true
  }

  assert {
    condition     = azurerm_key_vault.main.purge_protection_enabled == true
    error_message = "Key Vault purge protection should be enabled when flag is true"
  }
}

run "kv_public_access_disabled" {
  command = plan

  variables {
    resource_prefix                     = run.setup.resource_prefix
    environment                         = run.setup.environment
    instance                            = run.setup.instance
    location                            = run.setup.location
    resource_group                      = run.setup.resource_group
    current_user_oid                    = run.setup.current_user_oid
    should_enable_public_network_access = false
  }

  assert {
    condition     = azurerm_key_vault.main.public_network_access_enabled == false
    error_message = "Key Vault public access should be disabled"
  }

  assert {
    condition     = azurerm_key_vault.main.network_acls[0].default_action == "Deny"
    error_message = "Key Vault network ACL should deny when public access is disabled"
  }

  assert {
    condition     = azurerm_log_analytics_workspace.main.internet_ingestion_access_type == "Disabled"
    error_message = "Log Analytics ingestion access should be disabled when public access is disabled"
  }

  assert {
    condition     = azurerm_log_analytics_workspace.main.internet_query_access_type == "Disabled"
    error_message = "Log Analytics query access should be disabled when public access is disabled"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.public_network_access_enabled == false
    error_message = "ML workspace public access should be disabled"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.v1_legacy_mode_enabled == false
    error_message = "ML workspace v1 legacy mode should be disabled"
  }
}

run "kv_public_access_enabled" {
  command = plan

  variables {
    resource_prefix                     = run.setup.resource_prefix
    environment                         = run.setup.environment
    instance                            = run.setup.instance
    location                            = run.setup.location
    resource_group                      = run.setup.resource_group
    current_user_oid                    = run.setup.current_user_oid
    should_enable_public_network_access = true
  }

  assert {
    condition     = azurerm_key_vault.main.public_network_access_enabled == true
    error_message = "Key Vault public access should be enabled"
  }

  assert {
    condition     = azurerm_key_vault.main.network_acls[0].default_action == "Allow"
    error_message = "Key Vault network ACL should allow when public access is enabled"
  }

  assert {
    condition     = azurerm_log_analytics_workspace.main.internet_ingestion_access_type == "Enabled"
    error_message = "Log Analytics ingestion access should be enabled when public access is enabled"
  }

  assert {
    condition     = azurerm_log_analytics_workspace.main.internet_query_access_type == "Enabled"
    error_message = "Log Analytics query access should be enabled when public access is enabled"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.public_network_access_enabled == true
    error_message = "ML workspace public access should be enabled"
  }
}

run "aml_workspace_configuration" {
  command = plan

  override_resource {
    target          = azurerm_application_insights.main
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Insights/components/ai-test-dev-001"
    }
  }

  override_resource {
    target          = azurerm_key_vault.main
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.KeyVault/vaults/kvtestdev001"
    }
  }

  override_resource {
    target          = azurerm_storage_account.main
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Storage/storageAccounts/sttestdev001"
    }
  }

  override_resource {
    target          = azurerm_container_registry.main
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.ContainerRegistry/registries/acrtestdev001"
    }
  }

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.application_insights_id == azurerm_application_insights.main.id
    error_message = "ML workspace should use the platform Application Insights resource"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.location == run.setup.location
    error_message = "ML workspace should use the platform resource group location"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.resource_group_name == run.setup.resource_group.name
    error_message = "ML workspace should use the platform resource group"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.key_vault_id == azurerm_key_vault.main.id
    error_message = "ML workspace should use the platform Key Vault"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.storage_account_id == azurerm_storage_account.main.id
    error_message = "ML workspace should use the platform storage account"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.container_registry_id == azurerm_container_registry.main.id
    error_message = "ML workspace should use the platform container registry"
  }

  assert {
    condition     = one(azurerm_machine_learning_workspace.main.identity).type == "SystemAssigned"
    error_message = "ML workspace should use a system-assigned identity"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.sku_name == "Basic"
    error_message = "ML workspace should preserve the Basic SKU"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.kind == "Default"
    error_message = "ML workspace should preserve the Default kind"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.public_network_access_enabled == false
    error_message = "ML workspace should disable public network access by default"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.friendly_name == "mlw-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "ML workspace should preserve its friendly name"
  }
}

run "storage_security" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  assert {
    condition     = azurerm_storage_account.main.min_tls_version == "TLS1_2"
    error_message = "Storage account must enforce TLS 1.2 minimum"
  }

  assert {
    condition     = azurerm_storage_account.main.allow_nested_items_to_be_public == false
    error_message = "Storage account must not allow public blob access"
  }

  assert {
    condition     = azurerm_storage_account.main.shared_access_key_enabled == false
    error_message = "Storage account must disable shared access keys by default (Azure AD only)"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.storage_account_access_type == "Identity"
    error_message = "ML workspace must use identity-based system datastore auth by default"
  }
}

run "storage_shared_access_key_enabled" {
  command = plan

  variables {
    resource_prefix                         = run.setup.resource_prefix
    environment                             = run.setup.environment
    instance                                = run.setup.instance
    location                                = run.setup.location
    resource_group                          = run.setup.resource_group
    current_user_oid                        = run.setup.current_user_oid
    should_enable_storage_shared_access_key = true
    should_create_data_lake_storage         = true
  }

  assert {
    condition     = azurerm_storage_account.main.shared_access_key_enabled == true
    error_message = "Storage account shared access keys should be enabled when flag is true"
  }

  assert {
    condition     = azurerm_storage_account.data_lake[0].shared_access_key_enabled == true
    error_message = "Data lake storage account shared access keys should be enabled when flag is true"
  }

  assert {
    condition     = azurerm_machine_learning_workspace.main.storage_account_access_type == "AccessKey"
    error_message = "ML workspace must use accessKey system datastore auth when shared access keys are enabled"
  }
}

run "acr_security" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  assert {
    condition     = azurerm_container_registry.main.admin_enabled == false
    error_message = "ACR admin must be disabled"
  }

  assert {
    condition     = azurerm_container_registry.main.anonymous_pull_enabled == false
    error_message = "ACR anonymous pull must be disabled"
  }
}

run "data_lake_security" {
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
    condition     = azurerm_storage_account.data_lake[0].is_hns_enabled == true
    error_message = "Data lake storage account must have hierarchical namespace enabled"
  }

  assert {
    condition     = azurerm_storage_account.data_lake[0].min_tls_version == "TLS1_2"
    error_message = "Data lake storage account must enforce TLS 1.2 minimum"
  }

  assert {
    condition     = azurerm_storage_account.data_lake[0].allow_nested_items_to_be_public == false
    error_message = "Data lake storage account must not allow public blob access"
  }

  assert {
    condition     = azurerm_storage_account.data_lake[0].shared_access_key_enabled == false
    error_message = "Data lake storage account must disable shared access keys by default (Azure AD only)"
  }
}

run "data_lake_disabled_by_default" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  assert {
    condition     = length(azurerm_storage_account.data_lake) == 0
    error_message = "Data lake storage account should not exist when flag is false"
  }
}
