// Setup module for arc-runners tests
// Produces internally consistent mock dependency objects matching the module's input contracts

terraform {
  required_version = ">= 1.9.8, < 2.0"
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
}

resource "random_string" "prefix" {
  length  = 4
  special = false
  upper   = false
}

locals {
  subscription_id_part = "/subscriptions/00000000-0000-0000-0000-000000000000"
  resource_prefix      = "t${random_string.prefix.id}"
  environment          = "dev"
  instance             = "001"
  location             = "westus3"
  resource_group_name  = "rg-${local.resource_prefix}-${local.environment}-${local.instance}"
  resource_group_id    = "${local.subscription_id_part}/resourceGroups/${local.resource_group_name}"
  acr_name             = "acr${local.resource_prefix}${local.environment}${local.instance}"
  acr_id               = "${local.resource_group_id}/providers/Microsoft.ContainerRegistry/registries/${local.acr_name}"
  aks_name             = "aks-${local.resource_prefix}-${local.environment}-${local.instance}"
  aks_id               = "${local.resource_group_id}/providers/Microsoft.ContainerService/managedClusters/${local.aks_name}"
  kv_name              = "kv${local.resource_prefix}${local.environment}${local.instance}"
  kv_id                = "${local.resource_group_id}/providers/Microsoft.KeyVault/vaults/${local.kv_name}"
  uami_name            = "uami-gh-${local.resource_prefix}-${local.environment}-${local.instance}"
  uami_id              = "${local.resource_group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${local.uami_name}"
}

output "resource_prefix" {
  description = "Mock resource prefix for the arc-runners module under test."
  value       = local.resource_prefix
}

output "environment" {
  description = "Mock environment identifier for the arc-runners module under test."
  value       = local.environment
}

output "instance" {
  description = "Mock instance identifier for the arc-runners module under test."
  value       = local.instance
}

output "location" {
  description = "Mock Azure region for the arc-runners module under test."
  value       = local.location
}

output "resource_group" {
  description = "Mock resource group object passed to the arc-runners module under test."
  value = {
    id       = local.resource_group_id
    name     = local.resource_group_name
    location = local.location
  }
}

output "aks" {
  description = "Mock AKS cluster object the ARC controller and runner scale set deploy into."
  sensitive   = true
  value = {
    id                     = local.aks_id
    oidc_issuer_url        = "https://oidc.prod-aks.azure.com/00000000-0000-0000-0000-000000000000/"
    host                   = "https://${local.aks_name}.hcp.${local.location}.azmk8s.io:443"
    cluster_ca_certificate = base64encode("mock-ca-certificate")
    kube_config_raw        = "mock-kubeconfig"
  }
}

output "acr" {
  description = "Mock Azure Container Registry the runners publish signed images to."
  value = {
    id           = local.acr_id
    login_server = "${local.acr_name}.azurecr.io"
  }
}

output "key_vault" {
  description = "Mock Key Vault hosting the GitHub App private-key secret consumed by ARC."
  value = {
    id        = local.kv_id
    vault_uri = "https://${local.kv_name}.vault.azure.net/"
  }
}

output "github_oidc" {
  description = "Mock github-oidc module outputs supplied to the arc-runners workload-identity binding."
  value = {
    uami_id           = local.uami_id
    uami_client_id    = "00000000-0000-0000-0000-000000000001"
    uami_principal_id = "00000000-0000-0000-0000-000000000002"
  }
}

output "github_config_url" {
  description = "Mock GitHub repository or organization URL the runner scale set registers against."
  value       = "https://github.com/example-org/example-repo"
}

output "github_app_id" {
  description = "Mock GitHub App ID used by the runner scale set authentication."
  value       = "123456"
}

output "github_app_installation_id" {
  description = "Mock GitHub App installation ID for the target organization or repository."
  value       = "789012"
}

output "github_app_private_key_secret_id" {
  description = "Mock Azure Key Vault secret URI containing the GitHub App private key (PEM)."
  value       = "https://${local.kv_name}.vault.azure.net/secrets/gh-app-private-key/00000000000000000000000000000001"
}

