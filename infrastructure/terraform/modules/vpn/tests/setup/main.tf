// Setup module for VPN module tests
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
