// Test fixture: emits canonical core inputs and dependency objects for notation-akv module tests

terraform {
  required_version = ">= 1.9.8, < 2.0"
}

output "resource_prefix" {
  description = "Mock resource prefix for the notation-akv module under test."
  value       = "test"
}

output "environment" {
  description = "Mock environment identifier for the notation-akv module under test."
  value       = "dev"
}

output "instance" {
  description = "Mock instance identifier for the notation-akv module under test."
  value       = "001"
}

output "location" {
  description = "Mock Azure region for the notation-akv module under test."
  value       = "eastus2"
}

output "resource_group" {
  description = "Mock resource group object passed to the notation-akv module under test."
  value = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test"
    name     = "rg-test"
    location = "eastus2"
  }
}

output "aks" {
  description = "Mock AKS cluster identifiers used to issue federated credentials for workload-identity signers."
  value = {
    id              = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/aks-test"
    oidc_issuer_url = "https://oidc.test.example/"
  }
}

output "acr" {
  description = "Mock Azure Container Registry the signer identity will publish to."
  value = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ContainerRegistry/registries/testacr"
    login_server = "testacr.azurecr.io"
  }
}

output "key_vault_byo" {
  description = "Mock pre-existing Key Vault used by tests that exercise the bring-your-own-vault code path."
  value = {
    id        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.KeyVault/vaults/kv-byo"
    vault_uri = "https://kv-byo.vault.azure.net/"
  }
}

output "signer_subject_claims_single" {
  description = "Single-subject federated credential claim list used by single-signer tests."
  value       = ["system:serviceaccount:notation:signer-sa"]
}

output "signer_subject_claims_dual" {
  description = "Dual-subject federated credential claim list used by multi-signer tests."
  value = [
    "system:serviceaccount:notation:signer-sa",
    "system:serviceaccount:notation:release-sa",
  ]
}
