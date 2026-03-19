// Setup module for dataviewer module tests
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
  storage_account_name = "st${local.resource_prefix}${local.environment}${local.instance}"
}

output "resource_prefix" {
  value = local.resource_prefix
}

output "environment" {
  value = local.environment
}

output "instance" {
  value = local.instance
}

output "location" {
  value = local.location
}

output "resource_group" {
  value = {
    id       = local.resource_group_id
    name     = local.resource_group_name
    location = local.location
  }
}

output "virtual_network" {
  value = {
    id   = local.vnet_id
    name = local.vnet_name
  }
}

output "network_security_group" {
  value = {
    id = "${local.resource_group_id}/providers/Microsoft.Network/networkSecurityGroups/nsg-${local.resource_prefix}-${local.environment}-${local.instance}"
  }
}

output "nat_gateway" {
  value = {
    id = "${local.resource_group_id}/providers/Microsoft.Network/natGateways/ng-${local.resource_prefix}-${local.environment}-${local.instance}"
  }
}

output "log_analytics_workspace" {
  value = {
    id           = "${local.resource_group_id}/providers/Microsoft.OperationalInsights/workspaces/log-${local.resource_prefix}-${local.environment}-${local.instance}"
    workspace_id = "00000000-0000-0000-0000-000000000002"
  }
}

output "container_registry" {
  value = {
    id           = "${local.resource_group_id}/providers/Microsoft.ContainerRegistry/registries/acr${local.resource_prefix}${local.environment}${local.instance}"
    name         = "acr${local.resource_prefix}${local.environment}${local.instance}"
    login_server = "acr${local.resource_prefix}${local.environment}${local.instance}.azurecr.io"
  }
}

output "storage_account" {
  value = {
    id   = "${local.resource_group_id}/providers/Microsoft.Storage/storageAccounts/${local.storage_account_name}"
    name = local.storage_account_name
  }
}
