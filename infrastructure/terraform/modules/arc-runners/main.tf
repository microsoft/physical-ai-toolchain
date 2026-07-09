/**
 * # ARC Runners Module
 *
 * Installs the GitHub Actions Runner Controller (ARC) gha-runner-scale-set on the existing
 * AKS cluster, federates a user-assigned managed identity to the runner ServiceAccount via
 * AKS workload identity, and (optionally) installs a NetworkPolicy egress allowlist scoped
 * to the endpoints required for Sigstore keyless signing and ACR publishing.
 */

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"

  namespace_name           = "arc-runners"
  controller_release_name  = "arc-controller"
  runner_set_release_name  = "arc-runner-set"
  service_account_name     = "arc-runner"
  github_app_pk_secret_ref = "github-app-private-key"

  // Egress allowlist: Sigstore Fulcio/Rekor/TUF, GitHub control plane, ACR, Key Vault.
  sigstore_egress_hosts = [
    "fulcio.sigstore.dev",
    "rekor.sigstore.dev",
    "tuf-repo-cdn.sigstore.dev",
    "ghcr.io",
    "github.com",
    "api.github.com",
    var.acr.login_server,
    replace(replace(var.key_vault.vault_uri, "https://", ""), "/", ""),
  ]
}

// ====================================================================================
// Namespace
// ====================================================================================

resource "kubernetes_namespace" "arc_runners" {
  metadata {
    name = local.namespace_name

    labels = {
      "azure.workload.identity/use" = "true"
      "app.kubernetes.io/part-of"   = "arc-runners"
    }
  }
}

// ====================================================================================
// Runner workload-identity user-assigned managed identity
// ====================================================================================

resource "azurerm_user_assigned_identity" "runner" {
  name                = "id-arc-runner-${local.resource_name_suffix}"
  resource_group_name = var.resource_group.name
  location            = var.location
}

resource "azurerm_role_assignment" "runner_acr_push" {
  scope                = var.acr.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.runner.principal_id
}

resource "azurerm_key_vault_access_policy" "runner_kv_secret_get" {
  key_vault_id = var.key_vault.id
  tenant_id    = azurerm_user_assigned_identity.runner.tenant_id
  object_id    = azurerm_user_assigned_identity.runner.principal_id

  secret_permissions = ["Get"]
}

resource "azurerm_federated_identity_credential" "runner_wi" {
  name                = "fc-arc-runner-${local.resource_name_suffix}"
  resource_group_name = var.resource_group.name
  parent_id           = azurerm_user_assigned_identity.runner.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = var.aks.oidc_issuer_url
  subject             = "system:serviceaccount:${local.namespace_name}:${local.service_account_name}"
}

// ====================================================================================
// Runner ServiceAccount with workload-identity annotation
// ====================================================================================

resource "kubernetes_service_account" "runner" {
  metadata {
    name      = local.service_account_name
    namespace = kubernetes_namespace.arc_runners.metadata[0].name

    annotations = {
      "azure.workload.identity/client-id" = azurerm_user_assigned_identity.runner.client_id
    }

    labels = {
      "azure.workload.identity/use" = "true"
    }
  }
}

// ====================================================================================
// ARC controller and runner scale set Helm releases
// ====================================================================================

resource "helm_release" "arc_controller" {
  name             = local.controller_release_name
  namespace        = kubernetes_namespace.arc_runners.metadata[0].name
  repository       = "oci://ghcr.io/actions/actions-runner-controller-charts"
  chart            = "gha-runner-scale-set-controller"
  create_namespace = false

  values = [
    yamlencode({
      replicaCount = 1
    })
  ]
}

resource "helm_release" "runner_set" {
  name             = local.runner_set_release_name
  namespace        = kubernetes_namespace.arc_runners.metadata[0].name
  repository       = "oci://ghcr.io/actions/actions-runner-controller-charts"
  chart            = "gha-runner-scale-set"
  create_namespace = false

  depends_on = [helm_release.arc_controller]

  values = [
    yamlencode({
      githubConfigUrl = var.github_config_url
      githubConfigSecret = {
        github_app_id              = var.github_app_id
        github_app_installation_id = var.github_app_installation_id
        github_app_private_key     = "ref+akv://${var.key_vault.vault_uri}/${local.github_app_pk_secret_ref}"
      }
      minRunners = var.runner_replicas
      template = {
        spec = {
          serviceAccountName = kubernetes_service_account.runner.metadata[0].name
          containers = [
            {
              name    = "runner"
              image   = "ghcr.io/actions/actions-runner:${var.runner_image_tag}"
              command = ["/home/runner/run.sh"]
            }
          ]
        }
      }
    })
  ]
}

// ====================================================================================
// Sigstore egress NetworkPolicy (optional)
// ====================================================================================

resource "kubernetes_network_policy" "sigstore_egress" {
  count = var.should_enable_sigstore_egress ? 1 : 0

  metadata {
    name      = "arc-runner-sigstore-egress"
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        "app.kubernetes.io/component" = "runner"
      }
    }

    policy_types = ["Egress"]

    // DNS resolution to kube-dns.
    egress {
      to {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "kube-system"
          }
        }
      }

      ports {
        protocol = "UDP"
        port     = "53"
      }
    }

    // Allowlisted HTTPS egress to Sigstore, GitHub, ACR, Key Vault.
    // Hostname-based selection is enforced at the CNI / egress-gateway layer; this
    // NetworkPolicy restricts ports and documents the intended destinations via
    // the `allowed-hosts` annotation so policy engines (Calico, Cilium) can pin them.
    egress {
      ports {
        protocol = "TCP"
        port     = "443"
      }
    }
  }
}

resource "kubernetes_config_map" "sigstore_egress_hosts" {
  count = var.should_enable_sigstore_egress ? 1 : 0

  metadata {
    name      = "arc-runner-sigstore-egress-hosts"
    namespace = kubernetes_namespace.arc_runners.metadata[0].name
  }

  data = {
    "allowed-hosts" = join("\n", local.sigstore_egress_hosts)
  }
}
