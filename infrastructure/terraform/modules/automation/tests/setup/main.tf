// Setup module for automation module tests
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
  aks_cluster_name     = "aks-${local.resource_prefix}-${local.environment}-${local.instance}"
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

output "aks_cluster" {
  description = "Mock AKS cluster reference for test input."
  value = {
    id   = "${local.resource_group_id}/providers/Microsoft.ContainerService/managedClusters/${local.aks_cluster_name}"
    name = local.aks_cluster_name
  }
}

output "runbook_script_path" {
  description = "Path to stub runbook script for test execution."
  value       = "./tests/setup/scripts/stub.ps1"
}
