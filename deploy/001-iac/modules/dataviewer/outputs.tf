/**
 * # Dataviewer Module Outputs
 *
 * Typed object outputs for consumption by root module and deploy scripts.
 */

output "container_app_environment" {
  description = "Container Apps Environment details"
  value = {
    id             = azurerm_container_app_environment.main.id
    name           = azurerm_container_app_environment.main.name
    default_domain = azurerm_container_app_environment.main.default_domain
    static_ip      = azurerm_container_app_environment.main.static_ip_address
  }
}

output "backend" {
  description = "Backend Container App details"
  value = {
    id   = azurerm_container_app.backend.id
    name = azurerm_container_app.backend.name
    fqdn = azurerm_container_app.backend.ingress[0].fqdn
  }
}

output "frontend" {
  description = "Frontend Container App details"
  value = {
    id   = azurerm_container_app.frontend.id
    name = azurerm_container_app.frontend.name
    fqdn = azurerm_container_app.frontend.ingress[0].fqdn
    url  = "https://${azurerm_container_app.frontend.ingress[0].fqdn}"
  }
}

output "dataviewer_identity" {
  description = "Dataviewer managed identity for external role assignments"
  value = {
    id           = azurerm_user_assigned_identity.dataviewer.id
    principal_id = azurerm_user_assigned_identity.dataviewer.principal_id
    client_id    = azurerm_user_assigned_identity.dataviewer.client_id
  }
}

output "entra_id" {
  description = "Entra ID app registration details. Null when auth is disabled"
  value = var.should_deploy_dataviewer_auth ? {
    client_id = azuread_application.dataviewer[0].client_id
    tenant_id = data.azuread_client_config.current.tenant_id
  } : null
}
