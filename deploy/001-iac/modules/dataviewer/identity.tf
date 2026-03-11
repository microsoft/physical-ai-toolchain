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
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
}
