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
