// Dataviewer module container apps tests
// Validates container app ingress, image selection, and ACR registry configuration

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "random" {}

override_data {
  target = data.azuread_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
    object_id = "00000000-0000-0000-0000-000000000001"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Container Image Selection
// ============================================================

run "default_placeholder_images" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    network_security_group  = run.setup.network_security_group
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    storage_account         = run.setup.storage_account
    backend_image           = ""
    frontend_image          = ""
  }

  assert {
    condition     = length(azurerm_user_assigned_identity.caller) == 0 && output.caller_auth == null
    error_message = "Non-hosted deployments must not require a caller identity"
  }

  assert {
    condition     = azurerm_container_app.backend.template[0].container[0].image == "mcr.microsoft.com/k8se/quickstart:latest"
    error_message = "Backend should use placeholder image when no custom image is specified"
  }

  assert {
    condition     = azurerm_container_app.frontend.template[0].container[0].image == "mcr.microsoft.com/k8se/quickstart:latest"
    error_message = "Frontend should use placeholder image when no custom image is specified"
  }

  assert {
    condition     = azurerm_container_app.backend.ingress[0].allow_insecure_connections == false
    error_message = "Backend ingress must reject insecure connections"
  }

  assert {
    condition = alltrue([
      for name, value in {
        VLM_JUDGE_ENABLED   = "false"
        VLM_JUDGE_BACKEND   = "echo"
        VLM_JUDGE_CACHE_DIR = "/scratch/judge"
      } : lookup({ for setting in azurerm_container_app.backend.template[0].container[0].env : setting.name => setting.value }, name, null) == value
    ])
    error_message = "Bootstrap must keep judging disabled and use explicit echo and writable scratch defaults"
  }
}

run "custom_images" {
  command = plan

  override_resource {
    target          = azurerm_user_assigned_identity.dataviewer
    override_during = plan
    values = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-storage-test"
      client_id    = "00000000-0000-0000-0000-000000000041"
      principal_id = "00000000-0000-0000-0000-000000000042"
    }
  }

  override_resource {
    target          = azurerm_user_assigned_identity.caller[0]
    override_during = plan
    values = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-caller-test"
      client_id    = "00000000-0000-0000-0000-000000000031"
      principal_id = "00000000-0000-0000-0000-000000000032"
    }
  }

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    network_security_group        = run.setup.network_security_group
    log_analytics_workspace       = run.setup.log_analytics_workspace
    container_registry            = run.setup.container_registry
    storage_account               = run.setup.storage_account
    backend_image                 = "acrtestdev001.azurecr.io/dataviewer-backend:v1.0"
    frontend_image                = "acrtestdev001.azurecr.io/dataviewer-frontend:v1.0"
    should_deploy_dataviewer_auth = true
  }

  assert {
    condition     = azurerm_container_app.backend.template[0].container[0].image == "acrtestdev001.azurecr.io/dataviewer-backend:v1.0"
    error_message = "Backend should use custom image when specified"
  }

  assert {
    condition     = azurerm_container_app.frontend.template[0].container[0].image == "acrtestdev001.azurecr.io/dataviewer-frontend:v1.0"
    error_message = "Frontend should use custom image when specified"
  }

  assert {
    condition = (
      contains(azurerm_container_app.frontend.identity[0].identity_ids, azurerm_user_assigned_identity.caller[0].id) &&
      !contains(azurerm_container_app.backend.identity[0].identity_ids, azurerm_user_assigned_identity.caller[0].id)
    )
    error_message = "The dedicated caller identity belongs only to the frontend app"
  }

  assert {
    condition = alltrue([
      for container in azurerm_container_app.frontend.template[0].container : container.name != "caller-helper" || (
        toset([for setting in container.env : setting.name]) == toset(["DATAVIEWER_AZURE_CLIENT_ID", "DATAVIEWER_CALLER_CLIENT_ID"]) &&
        length(container.volume_mounts) == 0 && length(container.readiness_probe) == 0 && length(container.liveness_probe) == 0 &&
        container.cpu == 0.25 && container.memory == "0.5Gi"
      )
    ])
    error_message = "The helper must have explicit resources, only caller configuration and no public probes, storage or mounts"
  }

  assert {
    condition = alltrue([
      for name, value in {
        DATAVIEWER_CALLER_CLIENT_ID = "00000000-0000-0000-0000-000000000031"
        DATAVIEWER_CALLER_OBJECT_ID = "00000000-0000-0000-0000-000000000032"
      } : lookup({ for setting in one([for container in azurerm_container_app.backend.template[0].container : container if container.name == "backend"]).env : setting.name => setting.value }, name, null) == value
    ])
    error_message = "Backend caller ACL must use the dedicated identity, not the shared storage identity"
  }

  assert {
    condition = (
      output.caller_auth.mode == "managed_identity" &&
      output.caller_auth.caller_client_id == "00000000-0000-0000-0000-000000000031" &&
      output.caller_auth.caller_object_id == "00000000-0000-0000-0000-000000000032" &&
      length(keys(output.caller_auth)) == 6
    )
    error_message = "Caller output must expose the binding six-field managed-identity contract"
  }

  assert {
    condition     = azuread_application.dataviewer[0].api[0].requested_access_token_version == 2 && contains([for claim in azuread_application.dataviewer[0].optional_claims[0].access_token : claim.name], "idtyp")
    error_message = "The API registration must request v2 app-token identity claims"
  }

  assert {
    condition = length([
      for container in azurerm_container_app.frontend.template[0].container : container
      if container.name == "caller-helper" && container.image == "acrtestdev001.azurecr.io/dataviewer-backend:v1.0" && container.command == tolist(["python", "-m", "src.caller_auth_helper"])
    ]) == 1
    error_message = "Real hosted images must install the isolated helper from the backend runtime"
  }

  assert {
    condition = alltrue([
      for container in azurerm_container_app.frontend.template[0].container :
      container.name != "frontend" || anytrue([
        for probe in container.readiness_probe : probe.port == 8080 && probe.path == "/health" && probe.timeout == 10
      ])
    ])
    error_message = "Frontend readiness must observe nginx health with the bounded helper and storage-health budget"
  }
}

run "proxy_bound_auth_configuration" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    network_security_group        = run.setup.network_security_group
    log_analytics_workspace       = run.setup.log_analytics_workspace
    container_registry            = run.setup.container_registry
    storage_account               = run.setup.storage_account
    should_deploy_dataviewer_auth = true
  }

  assert {
    condition     = length(azurerm_container_app.frontend.template[0].container) == 1
    error_message = "Placeholder images must not try to start the installed helper module"
  }

  assert {
    condition = anytrue([
      for item in azurerm_container_app.backend.secret :
      item.name == "dataviewer-proxy-key"
    ])
    error_message = "Backend should define the Terraform-owned proxy credential"
  }

  assert {
    condition = anytrue([
      for item in azurerm_container_app.frontend.secret :
      item.name == "dataviewer-proxy-key"
    ])
    error_message = "Frontend should define the Terraform-owned proxy credential"
  }

  assert {
    condition = anytrue([
      for item in azurerm_container_app.frontend.secret :
      item.name == "microsoft-provider-authentication-secret"
    ])
    error_message = "Frontend should define the Terraform-owned Easy Auth credential"
  }

  assert {
    condition = !anytrue([
      for item in azurerm_container_app.backend.template[0].container[0].env :
      item.name == "DATAVIEWER_PROXY_KEY"
    ])
    error_message = "Backend must not use runtime shared-secret caller proof"
  }

  assert {
    condition = anytrue([
      for container in azurerm_container_app.frontend.template[0].container :
      container.name == "frontend" && anytrue([
        for item in container.env : item.name == "DATAVIEWER_CALLER_MODE" && item.value == "pending"
      ])
    ])
    error_message = "Hosted bootstrap must explicitly deny protected traffic until Easy Auth is verified"
  }

  assert {
    condition = !anytrue([
      for item in azurerm_container_app.frontend.template[0].container[0].env :
      item.name == "DATAVIEWER_PROXY_KEY"
    ])
    error_message = "Terraform bootstrap must not make the public frontend a proxy-proof oracle"
  }

  assert {
    condition = anytrue([
      for item in azurerm_container_app.backend.template[0].container[0].env :
      item.name == "DATAVIEWER_AUTH_PROVIDER" && item.value == "easy_auth"
    ])
    error_message = "Backend should select Easy Auth when authentication is enabled"
  }

  assert {
    condition     = azapi_resource.frontend_auth[0].body.properties.platform.enabled
    error_message = "Terraform must enable frontend Easy Auth before deployment injects proxy proof"
  }

  assert {
    condition     = azapi_resource.frontend_auth[0].body.properties.globalValidation.unauthenticatedClientAction == "RedirectToLoginPage"
    error_message = "Terraform must reject anonymous frontend requests with the Entra login flow"
  }

  assert {
    condition     = azapi_resource.frontend_auth[0].body.properties.identityProviders.azureActiveDirectory.registration.clientSecretSettingName == "microsoft-provider-authentication-secret"
    error_message = "Terraform Easy Auth must use the Container App secret created for the Entra application"
  }
}

// ============================================================
// ACR Registry Configuration
// ============================================================

run "acr_registry_enabled" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    network_security_group  = run.setup.network_security_group
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    storage_account         = run.setup.storage_account
  }

  assert {
    condition     = length(azurerm_container_app.backend.registry) == 1
    error_message = "Backend should have ACR registry block when login_server is configured"
  }

  assert {
    condition     = length(azurerm_container_app.frontend.registry) == 1
    error_message = "Frontend should have ACR registry block when login_server is configured"
  }
}

run "acr_registry_disabled" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    network_security_group  = run.setup.network_security_group
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry = {
      id           = run.setup.container_registry.id
      name         = run.setup.container_registry.name
      login_server = ""
    }
    storage_account = run.setup.storage_account
  }

  assert {
    condition     = length(azurerm_container_app.backend.registry) == 0
    error_message = "Backend should not have ACR registry block when login_server is empty"
  }

  assert {
    condition     = length(azurerm_container_app.frontend.registry) == 0
    error_message = "Frontend should not have ACR registry block when login_server is empty"
  }
}
