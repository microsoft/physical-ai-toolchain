/**
 * # Role Assignments
 *
 * This file consolidates all role assignments for the Platform module including:
 * - Key Vault access for current user and ML identity
 * - Storage Account access for ML identity
 * - Container Registry access for ML identity
 * - Grafana monitoring and admin access
 *
 * Note: Resources that require these role assignments (e.g., ML workspace) must
 * include depends_on references to ensure proper ordering
 */

// ============================================================
// Key Vault Role Assignments
// ============================================================

// Grant current user Key Vault Secrets Officer (for initial secret management)
resource "azurerm_role_assignment" "user_kv_officer" {
  count = var.should_add_current_user_key_vault_admin != null ? 1 : 0

  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.current_user_oid
}

// Grant ML identity Key Vault Secrets User (for workload access)
resource "azurerm_role_assignment" "ml_kv_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}

// ============================================================
// Resource Group Role Assignments
// ============================================================

// Grant ML identity Contributor at resource group level
// Required for ML workspace creation to read Key Vault and other resource metadata
resource "azurerm_role_assignment" "ml_rg_contributor" {
  scope                = var.resource_group.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}

// ============================================================
// Storage Account Role Assignments
// ============================================================

// Grant current user Storage Blob Data Contributor (for downloading job artifacts locally)
resource "azurerm_role_assignment" "user_storage_blob" {
  count = var.should_add_current_user_storage_blob ? 1 : 0

  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.current_user_oid
}

// Grant ML identity Storage Blob Data Contributor role
resource "azurerm_role_assignment" "ml_storage_blob" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}

// Grant ML identity Storage File Data SMB Share Contributor role
resource "azurerm_role_assignment" "ml_storage_file" {
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage File Data SMB Share Contributor"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}

// ============================================================
// OSMO Identity Role Assignments
// ============================================================

// Grant OSMO identity Storage Blob Data Contributor for workflow data access
resource "azurerm_role_assignment" "osmo_storage_blob_contributor" {
  count                = var.should_enable_osmo_identity ? 1 : 0
  scope                = azurerm_storage_account.main.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.osmo[0].principal_id
}

// Grant OSMO identity AcrPull role for pulling container images
resource "azurerm_role_assignment" "osmo_acr_pull" {
  count                = var.should_enable_osmo_identity ? 1 : 0
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.osmo[0].principal_id
}

// Grant OSMO identity AzureML Data Scientist role for MLflow experiment logging
// Provides workspace read access and ability to submit/manage experiments and runs
resource "azurerm_role_assignment" "osmo_ml_data_scientist" {
  count                = var.should_enable_osmo_identity ? 1 : 0
  scope                = azapi_resource.ml_workspace.id
  role_definition_name = "AzureML Data Scientist"
  principal_id         = azurerm_user_assigned_identity.osmo[0].principal_id
}

// ============================================================
// Container Registry Role Assignments
// ============================================================

// Grant ML identity AcrPush role (for training job image builds)
resource "azurerm_role_assignment" "ml_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}

// Grant ML identity AcrPull role (for pulling training images)
resource "azurerm_role_assignment" "ml_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}

// ============================================================
// OSMO Workload Identity Role Assignments
// ============================================================

// Grant OSMO identity Key Vault Secrets User for CSI secrets provider
resource "azurerm_role_assignment" "osmo_kv_secrets_user" {
  count = var.should_enable_osmo_identity ? 1 : 0

  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.osmo[0].principal_id
}

// ============================================================
// Grafana Role Assignments
// ============================================================

// Grant Grafana identity Monitoring Reader on resource group
resource "azurerm_role_assignment" "grafana_monitoring_reader" {
  count = var.should_deploy_grafana ? 1 : 0

  scope                = var.resource_group.id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_dashboard_grafana.main[0].identity[0].principal_id
}

// Grant Grafana identity Grafana Admin
resource "azurerm_role_assignment" "grafana_admin" {
  count = var.should_deploy_grafana ? 1 : 0

  scope                = azurerm_dashboard_grafana.main[0].id
  role_definition_name = "Grafana Admin"
  principal_id         = var.current_user_oid
}

// ============================================================
// Application Insights Role Assignments
// ============================================================

// Grant ML identity Monitoring Metrics Publisher on Application Insights
// Required for AzureML jobs to send telemetry when using managed identity
resource "azurerm_role_assignment" "ml_appinsights_publisher" {
  scope                = azurerm_application_insights.main.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.ml.principal_id
}
