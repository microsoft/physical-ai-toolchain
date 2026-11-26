/**
 * # Role Assignments
 *
 * Least-privilege RBAC for the private jumpbox managed identity and
 * Entra ID SSH login roles for operators.
 */

// ============================================================
// Jumpbox Managed Identity Role Assignments
// ============================================================

resource "azurerm_role_assignment" "jumpbox_aks_cluster_user" {
  count = var.should_deploy_jumpbox && var.aks_cluster_id != null ? 1 : 0

  scope                            = var.aks_cluster_id
  role_definition_name             = "Azure Kubernetes Service Cluster User Role"
  principal_id                     = azurerm_linux_virtual_machine.jumpbox[0].identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "jumpbox_acr_pull" {
  count = var.should_deploy_jumpbox && var.container_registry_id != null ? 1 : 0

  scope                            = var.container_registry_id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_linux_virtual_machine.jumpbox[0].identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "jumpbox_key_vault_secrets_user" {
  count = var.should_deploy_jumpbox && var.key_vault_id != null ? 1 : 0

  scope                            = var.key_vault_id
  role_definition_name             = "Key Vault Secrets User"
  principal_id                     = azurerm_linux_virtual_machine.jumpbox[0].identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "jumpbox_storage_blob_data_reader" {
  count = var.should_deploy_jumpbox && var.storage_account_id != null ? 1 : 0

  scope                            = var.storage_account_id
  role_definition_name             = "Storage Blob Data Reader"
  principal_id                     = azurerm_linux_virtual_machine.jumpbox[0].identity[0].principal_id
  skip_service_principal_aad_check = true
}

// ============================================================
// Entra ID SSH Login Role Assignments
// ============================================================

resource "azurerm_role_assignment" "jumpbox_current_user_admin_login" {
  count = local.should_assign_current_user_role ? 1 : 0

  scope                = azurerm_linux_virtual_machine.jumpbox[0].id
  role_definition_name = "Virtual Machine Administrator Login"
  principal_id         = local.current_user_principal_id
}

resource "azurerm_role_assignment" "jumpbox_admin_login" {
  for_each = var.should_deploy_jumpbox ? toset(var.jumpbox_admin_principal_ids) : toset([])

  scope                = azurerm_linux_virtual_machine.jumpbox[0].id
  role_definition_name = "Virtual Machine Administrator Login"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "jumpbox_user_login" {
  for_each = var.should_deploy_jumpbox ? toset(var.jumpbox_user_principal_ids) : toset([])

  scope                = azurerm_linux_virtual_machine.jumpbox[0].id
  role_definition_name = "Virtual Machine User Login"
  principal_id         = each.value
}
