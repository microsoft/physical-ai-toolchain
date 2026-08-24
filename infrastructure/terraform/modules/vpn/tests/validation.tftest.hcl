// VPN module variable validation tests
// Validates that invalid CIDR and SKU values are rejected by validation blocks

mock_provider "azurerm" {}

override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Invalid CIDR Format
// ============================================================

run "invalid_cidr_rejected" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    gateway_subnet_address_prefix = "not-a-cidr"
  }

  expect_failures = [var.gateway_subnet_address_prefix]
}

// ============================================================
// Invalid VPN Gateway SKU
// ============================================================

run "invalid_sku_rejected" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    vpn_gateway_config = {
      sku = "InvalidSku"
    }
  }

  expect_failures = [var.vpn_gateway_config]
}

// ============================================================
// Empty Zones Rejected
// ============================================================

run "empty_zones_rejected" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    vpn_gateway_config = {
      zones = []
    }
  }

  expect_failures = [var.vpn_gateway_config]
}

// ============================================================
// Invalid Revoked Client Certificate Thumbprint
// ============================================================

run "invalid_revoked_client_certificate_thumbprint_rejected" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    revoked_client_certificates = [{
      name       = "invalid-thumbprint"
      thumbprint = "0123456789abcdef0123456789abcdef0123456g"
    }]
  }

  expect_failures = [var.revoked_client_certificates]
}

// ============================================================
// Duplicate Revoked Client Certificate Names
// ============================================================

run "duplicate_revoked_client_certificate_names_rejected" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    revoked_client_certificates = [
      {
        name       = "duplicate-certificate"
        thumbprint = "0123456789abcdef0123456789abcdef01234567"
      },
      {
        name       = "duplicate-certificate"
        thumbprint = "89abcdef0123456789abcdef0123456789abcdef"
      },
    ]
  }

  expect_failures = [var.revoked_client_certificates]
}

// ============================================================
// Valid SKUs Accepted
// ============================================================

run "valid_skus_accepted" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    vpn_gateway_config = {
      sku        = "VpnGw2AZ"
      generation = "Generation2"
    }
  }

  assert {
    condition     = azurerm_virtual_network_gateway.main.sku == "VpnGw2AZ"
    error_message = "VPN Gateway should accept valid SKU VpnGw2AZ"
  }
}
