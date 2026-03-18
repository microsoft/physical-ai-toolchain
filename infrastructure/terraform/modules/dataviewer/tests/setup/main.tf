// Setup module for dataviewer module tests
// Generates mock input values including platform module dependency objects

terraform {
  required_providers {
    random = {
      source = "hashicorp/random"
    }
  }
}

resource "random_string" "prefix" {
  length  = 4
  special = false
  upper   = false
}

output "resource_prefix" {
  value = "t${random_string.prefix.id}"
}

output "environment" {
  value = "dev"
}

output "instance" {
  value = "001"
}

output "location" {
  value = "westus3"
}

output "resource_group" {
  value = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001"
    name     = "rg-test-dev-001"
    location = "westus3"
  }
}

output "virtual_network" {
  value = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001"
    name = "vnet-test-dev-001"
  }
}

output "network_security_group" {
  value = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/networkSecurityGroups/nsg-test-dev-001"
  }
}

output "nat_gateway" {
  value = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/natGateways/ng-test-dev-001"
  }
}

output "log_analytics_workspace" {
  value = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.OperationalInsights/workspaces/log-test-dev-001"
    workspace_id = "00000000-0000-0000-0000-000000000002"
  }
}

output "container_registry" {
  value = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.ContainerRegistry/registries/acrtestdev001"
    name         = "acrtestdev001"
    login_server = "acrtestdev001.azurecr.io"
  }
}

output "storage_account" {
  value = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Storage/storageAccounts/sttestdev001"
    name = "sttestdev001"
  }
}
