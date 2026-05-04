// Root precondition tests
// Validates that should_deploy_conversion_pipeline = true requires
// should_create_data_lake_storage = true. The check lives on a sibling
// terraform_data resource because module call blocks do not support
// lifecycle.precondition directly.

mock_provider "azurerm" {
  override_during = plan
}
mock_provider "azuread" {
  override_during = plan
}
mock_provider "azapi" {
  override_during = plan
}
mock_provider "msgraph" {
  override_during = plan
}
mock_provider "tls" {
  override_during = plan
}
mock_provider "random" {
  override_during = plan
}

override_data {
  target = module.platform.data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

// Bypass sil module count expressions that depend on platform try() outputs.
override_module {
  target = module.sil
  outputs = {
    aks_subnets = {
      aks = {
        id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-aks"
        name = "snet-aks"
      }
    }
    aks_cluster = {
      id                  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/aks-test"
      name                = "aks-test"
      fqdn                = "aks-test-dns.hcp.westus3.azmk8s.io"
      kubelet_identity    = null
      node_resource_group = "MC_rg-test_aks-test_westus3"
    }
    aks_oidc_issuer_url   = "https://westus3.oic.prod-aks.azure.com/00000000-0000-0000-0000-000000000000/"
    gpu_node_pool_subnets = {}
    node_pools            = {}
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Precondition: conversion pipeline requires platform data lake
// ============================================================

run "precondition_requires_data_lake" {
  command = plan

  variables {
    resource_prefix                   = run.setup.resource_prefix
    environment                       = run.setup.environment
    instance                          = run.setup.instance
    location                          = run.setup.location
    should_create_resource_group      = true
    should_deploy_conversion_pipeline = true
    should_create_data_lake_storage   = false
  }

  expect_failures = [
    terraform_data.conversion_pipeline_precondition,
  ]
}
