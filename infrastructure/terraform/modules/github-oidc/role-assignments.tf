/**
 * # Role Assignments
 * This file consolidates all role assignments for the GitHub OIDC module.
 */

// ============================================================
// ACR Role Assignments
// ============================================================

resource "azurerm_role_assignment" "acr_push" {
  count = var.should_grant_acr_push && var.acr != null ? 1 : 0

  scope                = var.acr.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.gh_oidc.principal_id
}
