// Platform module variable validation tests
// Validates that invalid nat_gateway_zones values are rejected by validation blocks

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

// ============================================================
// NAT Gateway Zones — Invalid Zone Value
// ============================================================

run "nat_gateway_zones_invalid_zone_rejected" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["4"]
  }

  expect_failures = [var.nat_gateway_zones]
}

// ============================================================
// NAT Gateway Zones — Non-numeric Value
// ============================================================

run "nat_gateway_zones_non_numeric_rejected" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["abc"]
  }

  expect_failures = [var.nat_gateway_zones]
}

// ============================================================
// NAT Gateway Zones — Duplicate Zones
// ============================================================

run "nat_gateway_zones_duplicates_rejected" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["1", "1"]
  }

  expect_failures = [var.nat_gateway_zones]
}

// ============================================================
// NAT Gateway Zones — Valid Single Zone
// ============================================================

run "nat_gateway_zones_single_zone_accepted" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["2"]
  }
}

// ============================================================
// NAT Gateway Zones — Valid Multiple Zones
// ============================================================

run "nat_gateway_zones_multiple_zones_accepted" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["1", "2", "3"]
  }
}

// ============================================================
// NAT Gateway Zones — Empty List (No AZ Support)
// ============================================================

run "nat_gateway_zones_empty_accepted" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = []
  }
}
