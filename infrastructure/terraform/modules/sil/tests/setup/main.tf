// Setup module for SIL module tests
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

output "current_user_oid" {
  description = "Stub user object ID for RBAC test assignments."
  value       = "00000000-0000-0000-0000-000000000001"
}

output "virtual_network" {
  description = "Mock virtual network reference for test input."
  value = {
    id   = local.vnet_id
    name = local.vnet_name
  }
}

output "subnets" {
  description = "Mock subnet references for test input."
  value = {
    main = {
      id   = "${local.vnet_id}/subnets/snet-${local.resource_prefix}-${local.environment}-${local.instance}"
      name = "snet-${local.resource_prefix}-${local.environment}-${local.instance}"
    }
    private_endpoints = {
      id   = "${local.vnet_id}/subnets/snet-pe-${local.resource_prefix}-${local.environment}-${local.instance}"
      name = "snet-pe-${local.resource_prefix}-${local.environment}-${local.instance}"
    }
  }
}

output "network_security_group" {
  description = "Mock network security group reference for test input."
  value = {
    id = "${local.resource_group_id}/providers/Microsoft.Network/networkSecurityGroups/nsg-${local.resource_prefix}-${local.environment}-${local.instance}"
  }
}

output "nat_gateway" {
  description = "Mock NAT gateway reference for test input."
  value = {
    id = "${local.resource_group_id}/providers/Microsoft.Network/natGateways/ng-${local.resource_prefix}-${local.environment}-${local.instance}"
  }
}

output "log_analytics_workspace" {
  description = "Mock Log Analytics workspace reference for test input."
  value = {
    id           = "${local.resource_group_id}/providers/Microsoft.OperationalInsights/workspaces/log-${local.resource_prefix}-${local.environment}-${local.instance}"
    workspace_id = "00000000-0000-0000-0000-000000000002"
  }
}

output "container_registry" {
  description = "Mock container registry reference for test input."
  value = {
    id           = "${local.resource_group_id}/providers/Microsoft.ContainerRegistry/registries/acr${local.resource_prefix}${local.environment}${local.instance}"
    name         = "acr${local.resource_prefix}${local.environment}${local.instance}"
    login_server = "acr${local.resource_prefix}${local.environment}${local.instance}.azurecr.io"
  }
}

output "private_dns_zones" {
  description = "Mock private DNS zone references for test input."
  value = {
    aks = {
      id   = "${local.resource_group_id}/providers/Microsoft.Network/privateDnsZones/privatelink.${local.location}.azmk8s.io"
      name = "privatelink.${local.location}.azmk8s.io"
    }
  }
}

output "monitor_workspace" {
  description = "Mock Azure Monitor workspace reference for test input."
  value = {
    id = "${local.resource_group_id}/providers/Microsoft.Monitor/accounts/azmon-${local.resource_prefix}-${local.environment}-${local.instance}"
  }
}

output "data_collection_endpoint" {
  description = "Mock data collection endpoint reference for test input."
  value = {
    id = "${local.resource_group_id}/providers/Microsoft.Insights/dataCollectionEndpoints/dce-${local.resource_prefix}-${local.environment}-${local.instance}"
  }
}

output "osmo_workload_identity" {
  description = "Mock OSMO workload identity reference for test input."
  value = {
    id           = "${local.resource_group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-osmo-${local.resource_prefix}-${local.environment}-${local.instance}"
    principal_id = "00000000-0000-0000-0000-000000000003"
    client_id    = "00000000-0000-0000-0000-000000000004"
    tenant_id    = "00000000-0000-0000-0000-000000000005"
  }
}
