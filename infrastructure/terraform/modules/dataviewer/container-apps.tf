/**
 * # Container Apps
 *
 * Defines the backend (FastAPI) and frontend (nginx + React) container apps.
 * Backend handles API requests and Azure Blob Storage access.
 * Frontend serves the React SPA and proxies /api requests to the backend.
 */

// ============================================================
// Backend Container App
// ============================================================

resource "azurerm_container_app" "backend" {
  name                         = "ca-backend-${local.resource_name_suffix}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.dataviewer.id]
  }

  dynamic "registry" {
    for_each = local.use_acr_images ? [1] : []
    content {
      server   = var.container_registry.login_server
      identity = azurerm_user_assigned_identity.dataviewer.id
    }
  }

  ingress {
    target_port                = 8000
    external_enabled           = false
    allow_insecure_connections = true

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "backend"
      image  = local.backend_image
      cpu    = var.backend_cpu
      memory = var.backend_memory

      env {
        name  = "BACKEND_HOST"
        value = "0.0.0.0"
      }
      env {
        name  = "BACKEND_PORT"
        value = "8000"
      }
      env {
        name  = "STORAGE_BACKEND"
        value = "azure"
      }
      env {
        name  = "AZURE_STORAGE_ACCOUNT_NAME"
        value = var.storage_account.name
      }
      env {
        name  = "AZURE_STORAGE_DATASET_CONTAINER"
        value = var.storage_dataset_container
      }
      env {
        name  = "AZURE_STORAGE_ANNOTATION_CONTAINER"
        value = var.storage_annotation_container
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.dataviewer.client_id
      }
      env {
        name  = "DATAVIEWER_AUTH_DISABLED"
        value = var.should_enable_internal && !var.should_deploy_dataviewer_auth ? "true" : "false"
      }
      env {
        name  = "CORS_ORIGINS"
        value = "https://*.${azurerm_container_app_environment.main.default_domain}"
      }

      liveness_probe {
        port      = 8000
        path      = "/health"
        transport = "HTTP"
      }

      readiness_probe {
        port      = 8000
        path      = "/health"
        transport = "HTTP"
      }
    }
  }

  // CI/CD manages the container image, env vars, and runtime config after IaC provisioning.
  // Prevent Terraform from reverting deploy-time changes.
  lifecycle {
    ignore_changes = [template, registry]
  }
}

// ============================================================
// Frontend Container App
// ============================================================

resource "azurerm_container_app" "frontend" {
  name                         = "ca-frontend-${local.resource_name_suffix}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.dataviewer.id]
  }

  dynamic "registry" {
    for_each = local.use_acr_images ? [1] : []
    content {
      server   = var.container_registry.login_server
      identity = azurerm_user_assigned_identity.dataviewer.id
    }
  }

  ingress {
    target_port      = 80
    external_enabled = true

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "frontend"
      image  = local.frontend_image
      cpu    = var.frontend_cpu
      memory = var.frontend_memory

      env {
        name  = "NGINX_BACKEND_HOST"
        value = azurerm_container_app.backend.ingress[0].fqdn
      }
      env {
        name  = "NGINX_BACKEND_SCHEME"
        value = "https"
      }
    }
  }

  // CI/CD manages the container image, env vars, and runtime config after IaC provisioning.
  // Prevent Terraform from reverting deploy-time changes.
  lifecycle {
    ignore_changes = [template, registry]
  }
}
