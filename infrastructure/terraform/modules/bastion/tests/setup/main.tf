// Setup module for Bastion module tests
// Generates mock input values with internally consistent IDs derived from the random prefix

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
  vnet_name            = "vnet-${local.resource_prefix}-${local.environment}-${local.instance}"
  vnet_id              = "${local.resource_group_id}/providers/Microsoft.Network/virtualNetworks/${local.vnet_name}"
  jumpbox_subnet_name  = "snet-${local.resource_prefix}-${local.environment}-${local.instance}"
  jumpbox_subnet_id    = "${local.vnet_id}/subnets/${local.jumpbox_subnet_name}"
}

output "resource_prefix" {
  description = "Generated resource naming prefix for test isolation."
  value       = local.resource_prefix
}

output "environment" {
  description = "Environment identifier for test configuration."
  value       = local.environment
}

output "instance" {
  description = "Instance identifier for test configuration."
  value       = local.instance
}

output "location" {
  description = "Azure region for test resources."
  value       = local.location
}

output "resource_group" {
  description = "Mock resource group object for test input."
  value = {
    id       = local.resource_group_id
    name     = local.resource_group_name
    location = local.location
  }
}

output "virtual_network" {
  description = "Mock virtual network reference for test input."
  value = {
    id   = local.vnet_id
    name = local.vnet_name
  }
}

output "jumpbox_subnet" {
  description = "Mock jumpbox subnet reference for test input."
  value = {
    id   = local.jumpbox_subnet_id
    name = local.jumpbox_subnet_name
  }
}

output "aks_cluster_id" {
  description = "Mock AKS cluster resource ID for RBAC testing."
  value       = "${local.resource_group_id}/providers/Microsoft.ContainerService/managedClusters/aks-${local.resource_prefix}-${local.environment}-${local.instance}"
}

output "container_registry_id" {
  description = "Mock Azure Container Registry resource ID for RBAC testing."
  value       = "${local.resource_group_id}/providers/Microsoft.ContainerRegistry/registries/cr${local.resource_prefix}${local.environment}${local.instance}"
}

output "key_vault_id" {
  description = "Mock Key Vault resource ID for RBAC testing."
  value       = "${local.resource_group_id}/providers/Microsoft.KeyVault/vaults/kv${local.resource_prefix}${local.environment}${local.instance}"
}

output "storage_account_id" {
  description = "Mock Storage Account resource ID for RBAC testing."
  value       = "${local.resource_group_id}/providers/Microsoft.Storage/storageAccounts/st${local.resource_prefix}${local.environment}${local.instance}"
}

output "test_ssh_public_key" {
  description = "Synthetic SSH public key string for plan-mode validation."
  value       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockTestKeyForTerraformPlanValidationOnly test@example.com"
}

output "admin_principal_id" {
  description = "Synthetic Entra object ID for admin login RBAC testing."
  value       = "11111111-1111-1111-1111-111111111111"
}

output "user_principal_id" {
  description = "Synthetic Entra object ID for user login RBAC testing."
  value       = "22222222-2222-2222-2222-222222222222"
}
