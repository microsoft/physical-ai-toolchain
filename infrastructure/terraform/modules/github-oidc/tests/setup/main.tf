// Setup module for github-oidc tests
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
}

output "resource_prefix" {
  description = "Mock resource prefix shared across module inputs."
  value       = local.resource_prefix
}

output "environment" {
  description = "Mock environment value."
  value       = local.environment
}

output "instance" {
  description = "Mock instance value."
  value       = local.instance
}

output "location" {
  description = "Mock Azure location."
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

output "container_registry" {
  description = "Mock ACR object matching the platform module output shape."
  value = {
    id           = "${local.resource_group_id}/providers/Microsoft.ContainerRegistry/registries/acr${local.resource_prefix}${local.environment}${local.instance}"
    name         = "acr${local.resource_prefix}${local.environment}${local.instance}"
    login_server = "acr${local.resource_prefix}${local.environment}${local.instance}.azurecr.io"
  }
}
