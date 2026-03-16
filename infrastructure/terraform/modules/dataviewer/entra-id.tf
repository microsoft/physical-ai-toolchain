/**
 * # Entra ID Authentication
 *
 * App registration and service principal for Dataviewer web application.
 * Only created when should_deploy_dataviewer_auth is true (public mode).
 * VNet-only deployments skip this and use DATAVIEWER_AUTH_DISABLED=true.
 */

// ============================================================
// Random UUIDs for Entra ID Resources
// ============================================================

resource "random_uuid" "dataviewer_scope_id" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0
}

resource "random_uuid" "dataviewer_role_viewer" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0
}

resource "random_uuid" "dataviewer_role_annotator" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0
}

resource "random_uuid" "dataviewer_role_admin" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0
}

// ============================================================
// App Registration
// ============================================================

data "azuread_client_config" "current" {}

resource "azuread_application" "dataviewer" {
  count = var.should_deploy_dataviewer_auth ? 1 : 0

  display_name     = "dataviewer-${local.resource_name_suffix}"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.current.object_id]

  single_page_application {
    redirect_uris = var.dataviewer_redirect_uris
  }

  // Easy Auth server-directed flow requires web redirect URIs and ID tokens
  web {
    redirect_uris = ["https://${azurerm_container_app.frontend.ingress[0].fqdn}/.auth/login/aad/callback"]

    implicit_grant {
      id_token_issuance_enabled = true
    }
  }

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Access Dataviewer API on behalf of the signed-in user"
      admin_consent_display_name = "Access Dataviewer API"
      enabled                    = true
      id                         = random_uuid.dataviewer_scope_id[0].result
      type                       = "User"
      user_consent_description   = "Access the Dataviewer API on your behalf"
      user_consent_display_name  = "Access Dataviewer"
      value                      = "access_as_user"
    }
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Read-only access to datasets, episodes, and annotations"
    display_name         = "Viewer"
    enabled              = true
    id                   = random_uuid.dataviewer_role_viewer[0].result
    value                = "Dataviewer.Viewer"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Read and write annotations, labels, and episode metadata"
    display_name         = "Annotator"
    enabled              = true
    id                   = random_uuid.dataviewer_role_annotator[0].result
    value                = "Dataviewer.Annotator"
  }

  app_role {
    allowed_member_types = ["User"]
    description          = "Full access including export, AI analysis, and configuration"
    display_name         = "Admin"
    enabled              = true
    id                   = random_uuid.dataviewer_role_admin[0].result
    value                = "Dataviewer.Admin"
  }

  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000" # Microsoft Graph

    resource_access {
      id   = "e1fe6dd8-ba31-4d61-89e7-88639da4683d" # User.Read (delegated)
      type = "Scope"
    }
  }

  feature_tags {
    enterprise = true
  }
}

// ============================================================
// Service Principal
// ============================================================

resource "azuread_service_principal" "dataviewer" {
  count     = var.should_deploy_dataviewer_auth ? 1 : 0
  client_id = azuread_application.dataviewer[0].client_id
}
