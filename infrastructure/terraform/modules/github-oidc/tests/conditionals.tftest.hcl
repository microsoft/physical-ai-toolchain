// github-oidc module conditional resource tests
// Validates federated credential expansion and AcrPush conditional behavior

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "random" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Federated Credential Expansion
// ============================================================

run "two_federated_subjects_create_two_fics" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    github_owner    = "microsoft"
    github_repo     = "physical-ai-toolchain"
    federated_subjects = {
      main-build = "repo:microsoft/physical-ai-toolchain:ref:refs/heads/main"
      production = "repo:microsoft/physical-ai-toolchain:environment:production"
    }
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.gh_oidc) == 2
    error_message = "Two federated subjects should produce two federated identity credentials"
  }

  assert {
    condition     = azurerm_federated_identity_credential.gh_oidc["main-build"].issuer == "https://token.actions.githubusercontent.com"
    error_message = "Federated credential issuer must be the GitHub Actions OIDC URL"
  }

  assert {
    condition     = contains(azurerm_federated_identity_credential.gh_oidc["main-build"].audience, "api://AzureADTokenExchange")
    error_message = "Federated credential audience must include api://AzureADTokenExchange"
  }
}

// ============================================================
// AcrPush Conditional (4 combinations)
// ============================================================

run "acr_supplied_push_enabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    github_owner          = "microsoft"
    github_repo           = "physical-ai-toolchain"
    federated_subjects    = { main = "repo:microsoft/physical-ai-toolchain:ref:refs/heads/main" }
    acr                   = run.setup.container_registry
    should_grant_acr_push = true
  }

  assert {
    condition     = length(azurerm_role_assignment.acr_push) == 1
    error_message = "AcrPush should be assigned when ACR is supplied and should_grant_acr_push is true"
  }
}

run "acr_supplied_push_disabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    github_owner          = "microsoft"
    github_repo           = "physical-ai-toolchain"
    federated_subjects    = { main = "repo:microsoft/physical-ai-toolchain:ref:refs/heads/main" }
    acr                   = run.setup.container_registry
    should_grant_acr_push = false
  }

  assert {
    condition     = length(azurerm_role_assignment.acr_push) == 0
    error_message = "AcrPush must not be assigned when should_grant_acr_push is false"
  }
}

run "acr_null_push_enabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    github_owner          = "microsoft"
    github_repo           = "physical-ai-toolchain"
    federated_subjects    = { main = "repo:microsoft/physical-ai-toolchain:ref:refs/heads/main" }
    acr                   = null
    should_grant_acr_push = true
  }

  assert {
    condition     = length(azurerm_role_assignment.acr_push) == 0
    error_message = "AcrPush must not be assigned when no ACR is supplied"
  }
}
