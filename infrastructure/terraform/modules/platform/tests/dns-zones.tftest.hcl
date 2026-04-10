// Platform module DNS zone count tests
// Validates private DNS zone counts based on feature flag combinations

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "random" {}

override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

variables {
  current_user_oid = "00000000-0000-0000-0000-000000000001"
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// PE on + AKS zone on + AMPLS on = 7 base + 1 AKS + 4 monitor = 12
run "all_dns_zones" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
    should_include_aks_dns_zone    = true
    should_deploy_ampls            = true
  }

  assert {
    condition     = length(azurerm_private_dns_zone.core) == 12
    error_message = "Expected 12 DNS zones (7 base + 1 AKS + 4 monitor)"
  }
}

// PE on + AKS zone off + AMPLS on = 7 base + 4 monitor = 11
run "no_aks_zone" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
    should_include_aks_dns_zone    = false
    should_deploy_ampls            = true
  }

  assert {
    condition     = length(azurerm_private_dns_zone.core) == 11
    error_message = "Expected 11 DNS zones (7 base + 4 monitor, no AKS)"
  }
}

// PE on + AKS zone on + AMPLS off = 7 base + 1 AKS = 8
run "no_ampls_zones" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
    should_include_aks_dns_zone    = true
    should_deploy_ampls            = false
  }

  assert {
    condition     = length(azurerm_private_dns_zone.core) == 8
    error_message = "Expected 8 DNS zones (7 base + 1 AKS, no AMPLS)"
  }
}

// PE on + AKS zone off + AMPLS off = 7 base only
run "base_zones_only" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
    should_include_aks_dns_zone    = false
    should_deploy_ampls            = false
  }

  assert {
    condition     = length(azurerm_private_dns_zone.core) == 7
    error_message = "Expected 7 base DNS zones"
  }
}

// PE off = 0 zones
run "pe_disabled_no_zones" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(azurerm_private_dns_zone.core) == 0
    error_message = "No DNS zones should exist when PE is disabled"
  }
}
