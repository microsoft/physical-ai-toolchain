/**
 * # OSMO Federated Identity Credentials
 *
 * Links Kubernetes ServiceAccounts to the OSMO managed identity,
 * enabling workload identity authentication for Azure Blob Storage.
 *
 * ServiceAccounts federated (names match Helm chart naming conventions):
 * - osmo-control-plane (control plane namespace) - osmo service chart
 * - router (control plane namespace) - osmo router chart
 * - osmo-operator-backend-listener (operator namespace) - backend-operator chart
 * - osmo-operator-backend-worker (operator namespace) - backend-operator chart
 */

// ============================================================
// OSMO Federated Identity Credentials
// ============================================================

locals {
  // Build map of ServiceAccounts requiring federated credentials
  // SA names match the actual names created by OSMO Helm charts
  osmo_federated_credentials = var.osmo_workload_identity != null && var.osmo_config.should_federate_identity ? {
    // Control plane namespace ServiceAccounts (created by osmo service/router charts)
    "osmo-control-plane" = {
      namespace = var.osmo_config.control_plane_namespace
      sa_name   = "osmo-control-plane"
    }
    "osmo-router" = {
      namespace = var.osmo_config.control_plane_namespace
      sa_name   = "router"
    }
    // Operator namespace ServiceAccounts (created by backend-operator chart)
    "osmo-backend-listener" = {
      namespace = var.osmo_config.operator_namespace
      sa_name   = "osmo-operator-backend-listener"
    }
    "osmo-backend-worker" = {
      namespace = var.osmo_config.operator_namespace
      sa_name   = "osmo-operator-backend-worker"
    }
    // Workflow namespace ServiceAccount for training jobs
    "osmo-workflow" = {
      namespace = var.osmo_config.workflows_namespace
      sa_name   = "osmo-workflow"
    }
  } : {}

  // Namespaces requiring default ServiceAccount federation for CSI secrets provider
  osmo_namespaces_for_default_sa = [
    var.osmo_config.control_plane_namespace,
    var.osmo_config.operator_namespace,
    var.osmo_config.workflows_namespace,
  ]
}

resource "azurerm_federated_identity_credential" "osmo" {
  for_each = local.osmo_federated_credentials

  name                = "osmo-${each.key}-fic"
  resource_group_name = var.resource_group.name
  parent_id           = var.osmo_workload_identity.id
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:${each.value.namespace}:${each.value.sa_name}"
  audience            = ["api://AzureADTokenExchange"]
}

// ============================================================
// OSMO Default ServiceAccount Federation (for CSI Secrets Provider)
// ============================================================
// Links default ServiceAccount in each namespace to OSMO identity
// enabling workload identity authentication for SecretProviderClass

resource "azurerm_federated_identity_credential" "osmo_default_sa" {
  for_each = var.osmo_config.should_federate_identity ? toset(local.osmo_namespaces_for_default_sa) : toset([])

  name                = "osmo-${each.key}-default-sa-fic"
  resource_group_name = var.resource_group.name
  parent_id           = var.osmo_workload_identity.id
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:${each.key}:default"
  audience            = ["api://AzureADTokenExchange"]
}
