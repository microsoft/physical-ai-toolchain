// Bastion module conditional resource tests
// Validates should_* booleans and optional RBAC role assignment counts

mock_provider "azurerm" {}

override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
    object_id = "00000000-0000-0000-0000-000000000000"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Bastion Only (Jumpbox Disabled, No SSH Key Required)
// ============================================================

run "bastion_without_jumpbox" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    virtual_network       = run.setup.virtual_network
    should_deploy_jumpbox = false
    jumpbox_subnet        = null
  }

  assert {
    condition     = length(azurerm_network_interface.jumpbox) == 0
    error_message = "Jumpbox NIC must not be created when should_deploy_jumpbox is false"
  }

  assert {
    condition     = length(azurerm_linux_virtual_machine.jumpbox) == 0
    error_message = "Jumpbox VM must not be created when should_deploy_jumpbox is false"
  }

  assert {
    condition     = length(azurerm_virtual_machine_extension.aad_ssh_login) == 0
    error_message = "AADSSHLoginForLinux extension must not be created when should_deploy_jumpbox is false"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_aks_cluster_user) == 0
    error_message = "No jumpbox RBAC assignments should be created when jumpbox is disabled"
  }
}

// ============================================================
// Bastion Without Managed NSG
// ============================================================

run "bastion_without_nsg" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    virtual_network           = run.setup.virtual_network
    should_create_bastion_nsg = false
    should_deploy_jumpbox     = false
  }

  assert {
    condition     = length(azurerm_network_security_group.bastion) == 0
    error_message = "Bastion NSG must not be created when should_create_bastion_nsg is false"
  }

  assert {
    condition     = length(azurerm_subnet_network_security_group_association.bastion) == 0
    error_message = "Bastion NSG association must not be created when should_create_bastion_nsg is false"
  }
}

// ============================================================
// Jumpbox With Optional RBAC Targets and Entra Login Principals
// ============================================================

run "jumpbox_with_rbac_targets" {
  command = plan

  variables {
    resource_prefix                        = run.setup.resource_prefix
    environment                            = run.setup.environment
    instance                               = run.setup.instance
    location                               = run.setup.location
    resource_group                         = run.setup.resource_group
    virtual_network                        = run.setup.virtual_network
    jumpbox_subnet                         = run.setup.jumpbox_subnet
    aks_cluster_id                         = run.setup.aks_cluster_id
    container_registry_id                  = run.setup.container_registry_id
    key_vault_id                           = run.setup.key_vault_id
    storage_account_id                     = run.setup.storage_account_id
    should_assign_current_user_admin_login = true
    jumpbox_admin_principal_ids            = [run.setup.admin_principal_id]
    jumpbox_user_principal_ids             = [run.setup.user_principal_id]
    jumpbox_config = {
      admin_ssh_public_key     = run.setup.test_ssh_public_key
      should_enable_cloud_init = false
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_aks_cluster_user) == 1
    error_message = "AKS Cluster User Role assignment should be created when aks_cluster_id is supplied"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_acr_pull) == 1
    error_message = "AcrPull role assignment should be created when container_registry_id is supplied"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_key_vault_secrets_user) == 1
    error_message = "Key Vault Secrets User role assignment should be created when key_vault_id is supplied"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_storage_blob_data_reader) == 1
    error_message = "Storage Blob Data Reader role assignment should be created when storage_account_id is supplied"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_current_user_admin_login) == 1
    error_message = "Current user VM admin login role should be created when should_assign_current_user_admin_login is true"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_admin_login) == 1
    error_message = "One VM admin login assignment should exist for jumpbox_admin_principal_ids"
  }

  assert {
    condition     = length(azurerm_role_assignment.jumpbox_user_login) == 1
    error_message = "One VM user login assignment should exist for jumpbox_user_principal_ids"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].custom_data == null
    error_message = "Jumpbox custom_data must be null when should_enable_cloud_init is false"
  }
}
