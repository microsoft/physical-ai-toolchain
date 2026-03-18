// Setup module for SIL module tests
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

output "current_user_oid" {
  value = "00000000-0000-0000-0000-000000000001"
}

output "virtual_network" {
  value = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001"
    name = "vnet-test-dev-001"
  }
}

output "subnets" {
  value = {
    main = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001/subnets/snet-test-dev-001"
      name = "snet-test-dev-001"
    }
    private_endpoints = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001/subnets/snet-pe-test-dev-001"
      name = "snet-pe-test-dev-001"
    }
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

output "private_dns_zones" {
  value = {
    aks = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/privateDnsZones/privatelink.westus3.azmk8s.io"
      name = "privatelink.westus3.azmk8s.io"
    }
  }
}

output "monitor_workspace" {
  value = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Monitor/accounts/azmon-test-dev-001"
  }
}

output "data_collection_endpoint" {
  value = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Insights/dataCollectionEndpoints/dce-test-dev-001"
  }
}

output "osmo_workload_identity" {
  value = {
    id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-osmo-test-dev-001"
    principal_id = "00000000-0000-0000-0000-000000000003"
    client_id    = "00000000-0000-0000-0000-000000000004"
    tenant_id    = "00000000-0000-0000-0000-000000000005"
  }
}
