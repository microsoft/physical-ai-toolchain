/**
 * # Role Assignments
 *
 * RBAC role assignments for the dataviewer managed identity including:
 * - AcrPull on Container Registry for image pulls
 * - Storage Blob Data Contributor on Storage Account for dataset read/write
 */

// ============================================================
// Container Registry Role Assignments
// ============================================================

resource "azurerm_role_assignment" "dataviewer_acr_pull" {
  scope                            = var.container_registry.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_user_assigned_identity.dataviewer.principal_id
  skip_service_principal_aad_check = true
}

// ============================================================
// Storage Account Role Assignments
// ============================================================

resource "azurerm_role_assignment" "dataviewer_storage_blob" {
  scope                            = var.storage_account.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azurerm_user_assigned_identity.dataviewer.principal_id
  skip_service_principal_aad_check = true
}

// ============================================================
// Data Lake Storage Role Assignments
// ============================================================

resource "azurerm_role_assignment" "dataviewer_data_lake_blob" {
  count = var.data_lake_storage_account != null ? 1 : 0

  scope                            = var.data_lake_storage_account.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azurerm_user_assigned_identity.dataviewer.principal_id
  skip_service_principal_aad_check = true
}
