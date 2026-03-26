/**
 * # Dataviewer Module
 *
 * Deploys the dataviewer application on Azure Container Apps with networking, identity, and app-level resources.
 *
 * Resources deployed:
 *
 * - Container Apps Environment with optional VNet integration
 * - Backend (FastAPI) and Frontend (nginx + React) container apps
 * - User-assigned managed identity for ACR and Storage access
 * - Optional Entra ID app registration for public access mode
 *
 * Supports internal (VNet/VPN) and external (public) deployment modes.
 */

// ============================================================
// Locals
// ============================================================

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"

  // Use the Container Apps quickstart image when no ACR image is specified.
  // CI/CD pushes real images and updates the container apps after IaC completes.
  placeholder_image = "mcr.microsoft.com/k8se/quickstart:latest"
  backend_image     = coalesce(var.backend_image, local.placeholder_image)
  frontend_image    = coalesce(var.frontend_image, local.placeholder_image)
  use_acr_images    = var.container_registry.login_server != ""
}

// ============================================================
// Container Apps Environment
// ============================================================

resource "azurerm_container_app_environment" "main" {
  name                               = "cae-${local.resource_name_suffix}"
  location                           = var.resource_group.location
  resource_group_name                = var.resource_group.name
  infrastructure_resource_group_name = "ME_cae-${local.resource_name_suffix}_${var.resource_group.name}_${var.resource_group.location}"
  infrastructure_subnet_id           = azurerm_subnet.container_apps.id
  internal_load_balancer_enabled     = var.should_enable_internal
  logs_destination                   = "log-analytics"
  log_analytics_workspace_id         = var.log_analytics_workspace.id

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}
