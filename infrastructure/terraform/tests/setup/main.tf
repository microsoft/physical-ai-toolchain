// Setup module for root integration tests
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
  resource_prefix = "t${random_string.prefix.id}"
  environment     = "dev"
  instance        = "001"
  location        = "westus3"
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
