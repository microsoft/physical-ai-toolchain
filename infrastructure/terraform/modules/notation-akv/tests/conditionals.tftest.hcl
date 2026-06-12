// Plan-mode tests verifying conditional resource creation for notation-akv module.
// Uses mock_provider with override_data for the azurerm_client_config data source.

mock_provider "azurerm" {
  override_data {
    target = data.azurerm_client_config.current
    values = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      client_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000000"
      object_id       = "00000000-0000-0000-0000-000000000003"
    }
  }
}

mock_provider "azuread" {}
mock_provider "random" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "disabled_creates_nothing" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    aks             = run.setup.aks
    acr             = run.setup.acr

    should_deploy         = false
    signer_subject_claims = run.setup.signer_subject_claims_single
  }

  assert {
    condition     = length(azurerm_key_vault.notation) == 0
    error_message = "Key Vault must not be created when should_deploy is false."
  }

  assert {
    condition     = length(azurerm_key_vault_key.notation_signing) == 0
    error_message = "Signing key must not be created when should_deploy is false."
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.notation_signer) == 0
    error_message = "Signer UAMI must not be created when should_deploy is false."
  }

  assert {
    condition     = length(azurerm_role_assignment.notation_signer_crypto_user) == 0
    error_message = "Crypto User role assignment must not be created when should_deploy is false."
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.notation_signer) == 0
    error_message = "Federated identity credentials must not be created when should_deploy is false."
  }
}

run "enabled_creates_managed_key_vault" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    aks             = run.setup.aks
    acr             = run.setup.acr

    should_deploy         = true
    signer_subject_claims = run.setup.signer_subject_claims_single
  }

  assert {
    condition     = length(azurerm_key_vault.notation) == 1
    error_message = "Module must provision a Key Vault when should_deploy is true and no BYO vault is supplied."
  }

  assert {
    condition     = azurerm_key_vault.notation[0].sku_name == "premium"
    error_message = "Notation Key Vault must use the Premium HSM SKU."
  }

  assert {
    condition     = azurerm_key_vault.notation[0].purge_protection_enabled == true
    error_message = "Notation Key Vault must enable purge protection."
  }

  assert {
    condition     = length(azurerm_key_vault_key.notation_signing) == 1
    error_message = "Module must create a signing key when should_deploy is true."
  }

  assert {
    condition     = azurerm_key_vault_key.notation_signing[0].key_type == "RSA-HSM"
    error_message = "Signing key must default to RSA-HSM."
  }

  assert {
    condition     = azurerm_key_vault_key.notation_signing[0].key_size == 3072
    error_message = "RSA-HSM signing key must default to 3072-bit size."
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.notation_signer) == 1
    error_message = "Module must create a signer UAMI when should_deploy is true."
  }

  assert {
    condition     = length(azurerm_role_assignment.notation_signer_crypto_user) == 1
    error_message = "Module must grant the signer UAMI Key Vault Crypto User on the vault."
  }

  assert {
    condition     = azurerm_role_assignment.notation_signer_crypto_user[0].role_definition_name == "Key Vault Crypto User"
    error_message = "Signer UAMI must receive the Key Vault Crypto User role."
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.notation_signer) == 1
    error_message = "Module must create one federated identity credential per signer subject claim."
  }
}

run "byo_key_vault_skips_vault_creation" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    aks             = run.setup.aks
    acr             = run.setup.acr

    should_deploy         = true
    signer_subject_claims = run.setup.signer_subject_claims_single
    key_vault             = run.setup.key_vault_byo
  }

  assert {
    condition     = length(azurerm_key_vault.notation) == 0
    error_message = "Module must not create a Key Vault when a BYO vault is supplied."
  }

  assert {
    condition     = length(azurerm_key_vault_key.notation_signing) == 1
    error_message = "Signing key must still be created against the BYO Key Vault."
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.notation_signer) == 1
    error_message = "Signer UAMI must still be created when using a BYO Key Vault."
  }

  assert {
    condition     = length(azurerm_role_assignment.notation_signer_crypto_user) == 1
    error_message = "Crypto User role assignment must still be created when using a BYO Key Vault."
  }
}

run "multiple_subject_claims_create_multiple_fics" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    aks             = run.setup.aks
    acr             = run.setup.acr

    should_deploy         = true
    signer_subject_claims = run.setup.signer_subject_claims_dual
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.notation_signer) == 2
    error_message = "Module must create one federated identity credential per signer subject claim."
  }
}
