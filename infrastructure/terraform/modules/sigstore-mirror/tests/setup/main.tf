// Test fixture: emits canonical core inputs for sigstore-mirror module tests

terraform {
  required_version = ">= 1.9.8, < 2.0"
}

output "resource_prefix" {
  description = "Mock resource prefix for the sigstore-mirror module under test."
  value       = "test"
}

output "environment" {
  description = "Mock environment identifier for the sigstore-mirror module under test."
  value       = "dev"
}

output "instance" {
  description = "Mock instance identifier for the sigstore-mirror module under test."
  value       = "001"
}

output "location" {
  description = "Mock Azure region for the sigstore-mirror module under test."
  value       = "eastus2"
}

output "resource_group" {
  description = "Mock resource group object passed to the sigstore-mirror module under test."
  value = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test"
    name     = "rg-test"
    location = "eastus2"
  }
}
