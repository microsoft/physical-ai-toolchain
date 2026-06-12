// arc-runners module conditional resource tests
// Validates sigstore egress toggling and federated credential subject wiring

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "kubernetes" {}
mock_provider "helm" {}
mock_provider "random" {}

override_resource {
  target = azurerm_user_assigned_identity.runner
  values = {
    principal_id = "00000000-0000-0000-0000-000000000010"
    client_id    = "00000000-0000-0000-0000-000000000011"
    tenant_id    = "00000000-0000-0000-0000-000000000012"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Sigstore Egress Toggle
// ============================================================

run "sigstore_egress_enabled" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    aks                           = run.setup.aks
    acr                           = run.setup.acr
    key_vault                     = run.setup.key_vault
    github_oidc                   = run.setup.github_oidc
    github_config_url             = run.setup.github_config_url
    github_app_id                 = run.setup.github_app_id
    github_app_installation_id    = run.setup.github_app_installation_id
    should_enable_sigstore_egress = true
  }

  assert {
    condition     = length(kubernetes_network_policy.sigstore_egress) == 1
    error_message = "Sigstore egress NetworkPolicy must be created when should_enable_sigstore_egress is true"
  }

  assert {
    condition     = length(kubernetes_config_map.sigstore_egress_hosts) == 1
    error_message = "Sigstore egress hosts ConfigMap must be created when should_enable_sigstore_egress is true"
  }
}

run "sigstore_egress_disabled" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    aks                           = run.setup.aks
    acr                           = run.setup.acr
    key_vault                     = run.setup.key_vault
    github_oidc                   = run.setup.github_oidc
    github_config_url             = run.setup.github_config_url
    github_app_id                 = run.setup.github_app_id
    github_app_installation_id    = run.setup.github_app_installation_id
    should_enable_sigstore_egress = false
  }

  assert {
    condition     = length(kubernetes_network_policy.sigstore_egress) == 0
    error_message = "Sigstore egress NetworkPolicy must not be created when should_enable_sigstore_egress is false"
  }

  assert {
    condition     = length(kubernetes_config_map.sigstore_egress_hosts) == 0
    error_message = "Sigstore egress hosts ConfigMap must not be created when should_enable_sigstore_egress is false"
  }
}

// ============================================================
// Federated Identity Credential Subject Wiring
// ============================================================

run "federated_subject_targets_runner_service_account" {
  command = plan

  variables {
    resource_prefix            = run.setup.resource_prefix
    environment                = run.setup.environment
    instance                   = run.setup.instance
    location                   = run.setup.location
    resource_group             = run.setup.resource_group
    aks                        = run.setup.aks
    acr                        = run.setup.acr
    key_vault                  = run.setup.key_vault
    github_oidc                = run.setup.github_oidc
    github_config_url          = run.setup.github_config_url
    github_app_id              = run.setup.github_app_id
    github_app_installation_id = run.setup.github_app_installation_id
  }

  assert {
    condition     = azurerm_federated_identity_credential.runner_wi.subject == "system:serviceaccount:arc-runners:arc-runner"
    error_message = "Federated credential subject must target the arc-runner service account in the arc-runners namespace"
  }

  assert {
    condition     = contains(azurerm_federated_identity_credential.runner_wi.audience, "api://AzureADTokenExchange")
    error_message = "Federated credential audience must include api://AzureADTokenExchange"
  }

  assert {
    condition     = azurerm_federated_identity_credential.runner_wi.issuer == run.setup.aks.oidc_issuer_url
    error_message = "Federated credential issuer must be the AKS OIDC issuer URL"
  }
}
