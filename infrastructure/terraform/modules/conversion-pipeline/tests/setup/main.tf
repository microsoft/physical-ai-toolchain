// Setup module for conversion-pipeline tests
// Generates synthetic IDs/values matching the dependency object schemas
// expected by the module's variables.deps.tf.

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
  numeric = false
}

locals {
  subscription_id_part   = "/subscriptions/00000000-0000-0000-0000-000000000000"
  resource_prefix        = "t${random_string.prefix.id}"
  environment            = "dev"
  instance               = "001"
  location               = "westus3"
  resource_group_name    = "rg-${local.resource_prefix}-${local.environment}-${local.instance}"
  resource_group_id      = "${local.subscription_id_part}/resourceGroups/${local.resource_group_name}"
  data_lake_account_name = "stdl${local.resource_prefix}${local.environment}${local.instance}"
  data_lake_account_id   = "${local.resource_group_id}/providers/Microsoft.Storage/storageAccounts/${local.data_lake_account_name}"
  datasets_container_id  = "${local.data_lake_account_id}/blobServices/default/containers/datasets"
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
  description = "Mock resource group object."
  value = {
    id       = local.resource_group_id
    name     = local.resource_group_name
    location = local.location
  }
}

output "data_lake_storage_account" {
  description = "Mock platform-owned data-lake (stdl...) account."
  value = {
    id   = local.data_lake_account_id
    name = local.data_lake_account_name
  }
}

output "datasets_container" {
  description = "Mock datasets container on the data-lake account."
  value = {
    id   = local.datasets_container_id
    name = "datasets"
  }
}
