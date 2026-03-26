// VPN standalone variable validation tests
// Validates that invalid CIDR and SKU values are rejected by validation blocks

mock_provider "azurerm" {}

override_data {
  target = module.vpn.data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

override_data {
  target = data.azurerm_resource_group.this
  values = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001"
    name     = "rg-test-dev-001"
    location = "westus3"
  }
}

override_data {
  target = data.azurerm_virtual_network.this
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001"
    name = "vnet-test-dev-001"
  }
}

// ============================================================
// Invalid CIDR Format
// ============================================================

run "invalid_cidr_rejected" {
  command = plan

  variables {
    resource_prefix               = "test"
    environment                   = "dev"
    instance                      = "001"
    location                      = "westus3"
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
    resource_prefix = "test"
    environment     = "dev"
    instance        = "001"
    location        = "westus3"
    vpn_gateway_config = {
      sku = "InvalidSku"
    }
  }

  expect_failures = [var.vpn_gateway_config]
}

// ============================================================
// Valid VPN Gateway SKU
// ============================================================

run "valid_az_sku_accepted" {
  command = plan

  variables {
    resource_prefix = "test"
    environment     = "dev"
    instance        = "001"
    location        = "westus3"
    vpn_gateway_config = {
      sku        = "VpnGw2AZ"
      generation = "Generation2"
    }
  }

  assert {
    condition     = output.vpn_gateway.sku == "VpnGw2AZ"
    error_message = "Standalone VPN deployment should accept valid AZ SKU VpnGw2AZ"
  }
}
