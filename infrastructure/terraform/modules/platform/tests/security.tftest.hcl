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
  current_user_oid = "00000000-0000-0000-0000-000000000001"
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
