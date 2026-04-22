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
  subscription_id_part = "/subscriptions/00000000-0000-0000-0000-000000000000"
  resource_prefix      = "t${random_string.prefix.id}"
  environment          = "dev"
  instance             = "001"
  location             = "westus3"
  resource_group_name  = "rg-${local.resource_prefix}-${local.environment}-${local.instance}"
  resource_group_id    = "${local.subscription_id_part}/resourceGroups/${local.resource_group_name}"
  vnet_name            = "vnet-${local.resource_prefix}-${local.environment}-${local.instance}"
  vnet_id              = "${local.resource_group_id}/providers/Microsoft.Network/virtualNetworks/${local.vnet_name}"
  pe_subnet_name       = "snet-pe"
  pe_subnet_id         = "${local.vnet_id}/subnets/${local.pe_subnet_name}"
  blob_zone_id         = "${local.resource_group_id}/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
  dfs_zone_id          = "${local.resource_group_id}/providers/Microsoft.Network/privateDnsZones/privatelink.dfs.core.windows.net"
  law_id               = "${local.resource_group_id}/providers/Microsoft.OperationalInsights/workspaces/law-${local.resource_prefix}"
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

output "virtual_network" {
  description = "Mock virtual network reference."
  value = {
    id   = local.vnet_id
    name = local.vnet_name
  }
}

output "subnets" {
  description = "Mock subnets object exposing the private endpoints subnet."
  value = {
    private_endpoints = {
      id   = local.pe_subnet_id
      name = local.pe_subnet_name
    }
  }
}

output "private_dns_zones" {
  description = "Mock private DNS zones for blob and dfs subresources."
  value = {
    storage_blob = {
      id   = local.blob_zone_id
      name = "privatelink.blob.core.windows.net"
    }
    storage_dfs = {
      id   = local.dfs_zone_id
      name = "privatelink.dfs.core.windows.net"
    }
  }
}

output "log_analytics_workspace" {
  description = "Mock Log Analytics workspace reference."
  value = {
    id           = local.law_id
    workspace_id = "00000000-0000-0000-0000-000000000001"
  }
}
