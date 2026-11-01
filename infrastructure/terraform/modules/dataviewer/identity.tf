/**
 * # Managed Identity
 *
 * User-assigned managed identity for the dataviewer Container Apps.
 * Used for ACR image pulls and Storage Account blob access.
 */

// ============================================================
// User Assigned Managed Identity
// ============================================================

resource "azurerm_user_assigned_identity" "dataviewer" {
  name                = "id-dataviewer-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
}

resource "azurerm_user_assigned_identity" "caller" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0

  name                = "id-dataviewer-caller-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
}
