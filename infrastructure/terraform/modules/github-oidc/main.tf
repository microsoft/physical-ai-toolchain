/**
 * # GitHub OIDC Module
 *
 * This file creates the GitHub Actions workload identity for this repository:
 * - User-assigned managed identity consumed by the container-publish reusable workflows
 * - Federated identity credentials binding GitHub OIDC subjects to the UAMI
 *
 * Forks and downstream consumers add their own github-oidc instantiations; the module
 * does not assume any particular fork hosts publish-capable workflows.
 */

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"
}

// ============================================================
// User-Assigned Managed Identity
// ============================================================

resource "azurerm_user_assigned_identity" "gh_oidc" {
  name                = "id-gh-oidc-${local.resource_name_suffix}"
  resource_group_name = var.resource_group.name
  location            = var.resource_group.location
}

// ============================================================
// GitHub Federated Identity Credentials
// ============================================================

resource "azurerm_federated_identity_credential" "gh_oidc" {
  for_each = var.federated_subjects

  name                = "gh-oidc-${each.key}-fic"
  resource_group_name = var.resource_group.name
  parent_id           = azurerm_user_assigned_identity.gh_oidc.id
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = each.value
  audience            = ["api://AzureADTokenExchange"]
}
