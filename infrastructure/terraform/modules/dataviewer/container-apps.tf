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

  dynamic "secret" {
    for_each = var.should_deploy_dataviewer_auth ? [random_password.dataviewer_proxy_key[0].result] : []
    content {
      name  = "dataviewer-proxy-key"
      value = secret.value
    }
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
    allow_insecure_connections = false

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
        name  = "VLM_JUDGE_ENABLED"
        value = "false"
      }
      env {
        name  = "VLM_JUDGE_BACKEND"
        value = "echo"
      }
      env {
        name  = "VLM_JUDGE_CACHE_DIR"
        value = "/scratch/judge"
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
      dynamic "env" {
        for_each = var.should_deploy_dataviewer_auth ? [1] : []
        content {
          name  = "DATAVIEWER_AUTH_PROVIDER"
          value = "easy_auth"
        }
      }
      dynamic "env" {
        for_each = var.should_deploy_dataviewer_auth ? {
          DATAVIEWER_AZURE_TENANT_ID  = data.azuread_client_config.current.tenant_id
          DATAVIEWER_AZURE_CLIENT_ID  = azuread_application.dataviewer[0].client_id
          DATAVIEWER_CALLER_OBJECT_ID = azurerm_user_assigned_identity.caller[0].principal_id
          DATAVIEWER_CALLER_CLIENT_ID = azurerm_user_assigned_identity.caller[0].client_id
        } : {}
        content {
          name  = env.key
          value = env.value
        }
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
    type = "UserAssigned"
    identity_ids = concat(
      [azurerm_user_assigned_identity.dataviewer.id],
      azurerm_user_assigned_identity.caller[*].id
    )
  }

  dynamic "secret" {
    for_each = var.should_deploy_dataviewer_auth ? [random_password.dataviewer_proxy_key[0].result] : []
    content {
      name  = "dataviewer-proxy-key"
      value = secret.value
    }
  }

  dynamic "secret" {
    for_each = var.should_deploy_dataviewer_auth ? [azuread_application_password.dataviewer_easy_auth[0].value] : []
    content {
      name  = "microsoft-provider-authentication-secret"
      value = secret.value
    }
  }

  dynamic "registry" {
    for_each = local.use_acr_images ? [1] : []
    content {
      server   = var.container_registry.login_server
      identity = azurerm_user_assigned_identity.dataviewer.id
    }
  }

  ingress {
    target_port                = 8080
    external_enabled           = true
    allow_insecure_connections = false

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
      env {
        name  = "DATAVIEWER_CALLER_MODE"
        value = var.should_deploy_dataviewer_auth ? "pending" : "off"
      }

      dynamic "liveness_probe" {
        for_each = local.frontend_image != local.placeholder_image ? [1] : []
        content {
          port             = 8080
          path             = "/"
          transport        = "HTTP"
          timeout          = 2
          interval_seconds = 30
        }
      }

      dynamic "readiness_probe" {
        for_each = local.frontend_image != local.placeholder_image ? [1] : []
        content {
          port             = 8080
          path             = "/health"
          transport        = "HTTP"
          timeout          = 10
          interval_seconds = 30
        }
      }
    }

    dynamic "container" {
      for_each = var.should_deploy_dataviewer_auth && local.backend_image != local.placeholder_image && local.frontend_image != local.placeholder_image ? [1] : []
      content {
        name    = "caller-helper"
        image   = local.backend_image
        command = ["python", "-m", "src.caller_auth_helper"]
        cpu     = 0.25
        memory  = "0.5Gi"

        env {
          name  = "DATAVIEWER_AZURE_CLIENT_ID"
          value = azuread_application.dataviewer[0].client_id
        }
        env {
          name  = "DATAVIEWER_CALLER_CLIENT_ID"
          value = azurerm_user_assigned_identity.caller[0].client_id
        }
      }
    }
  }

  // CI/CD manages the container image, env vars, and runtime config after IaC provisioning.
  // Prevent Terraform from reverting deploy-time changes.
  lifecycle {
    ignore_changes = [template, registry]
  }
}

resource "azapi_resource" "frontend_auth" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0

  type      = "Microsoft.App/containerApps/authConfigs@2025-01-01"
  name      = "current"
  parent_id = azurerm_container_app.frontend.id

  body = {
    properties = {
      globalValidation = {
        redirectToProvider          = "azureactivedirectory"
        unauthenticatedClientAction = "RedirectToLoginPage"
      }
      httpSettings = {
        requireHttps = true
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = azuread_application.dataviewer[0].client_id
            clientSecretSettingName = "microsoft-provider-authentication-secret"
            openIdIssuer            = "https://login.microsoftonline.com/${data.azuread_client_config.current.tenant_id}/v2.0"
          }
        }
      }
      platform = {
        enabled = true
      }
    }
  }
}
