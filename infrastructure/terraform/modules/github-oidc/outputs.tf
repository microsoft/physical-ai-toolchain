/**
 * # GitHub OIDC Outputs
 * Identifiers consumed by downstream modules and surfaced for GitHub Actions secret wiring.
 */

output "client_id" {
  description = "AAD client ID of the user-assigned managed identity (use as AZURE_CLIENT_ID)."
  value       = azurerm_user_assigned_identity.gh_oidc.client_id
}

output "principal_id" {
  description = "Object ID (principal ID) of the user-assigned managed identity."
  value       = azurerm_user_assigned_identity.gh_oidc.principal_id
}

output "tenant_id" {
  description = "AAD tenant ID associated with the managed identity."
  value       = azurerm_user_assigned_identity.gh_oidc.tenant_id
}

output "user_assigned_identity" {
  description = "Full user-assigned managed identity object for downstream module wiring."
  value = {
    id           = azurerm_user_assigned_identity.gh_oidc.id
    name         = azurerm_user_assigned_identity.gh_oidc.name
    client_id    = azurerm_user_assigned_identity.gh_oidc.client_id
    principal_id = azurerm_user_assigned_identity.gh_oidc.principal_id
    tenant_id    = azurerm_user_assigned_identity.gh_oidc.tenant_id
  }
}
