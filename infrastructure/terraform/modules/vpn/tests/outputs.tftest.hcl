// VPN module output structure tests
// Validates output contracts including gateway subnet and connection outputs

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
// Outputs Without Sites
// ============================================================

run "outputs_without_sites" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
  }

  assert {
    condition     = output.gateway_subnet.name == "GatewaySubnet"
    error_message = "gateway_subnet.name should be GatewaySubnet"
  }

  assert {
    condition     = output.vpn_gateway.sku == "VpnGw1AZ"
    error_message = "vpn_gateway.sku should default to VpnGw1AZ"
  }

  assert {
    condition     = output.vpn_gateway_public_ip.zones == tolist(["1", "2", "3"])
    error_message = "vpn_gateway_public_ip.zones should default to [1, 2, 3]"
  }

  assert {
    condition     = output.site_connections == {}
    error_message = "site_connections should be empty when no sites are configured"
  }

  assert {
    condition     = output.local_network_gateways == {}
    error_message = "local_network_gateways should be empty when no sites are configured"
  }
}

// ============================================================
// Outputs With Sites
// ============================================================

run "outputs_with_sites" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    vpn_site_connections = [{
      name                 = "TestSite"
      address_spaces       = ["10.1.0.0/16"]
      shared_key_reference = "test_key"
      gateway_ip_address   = "1.2.3.4"
    }]
    vpn_site_shared_keys = {
      test_key = "dummyKey123"
    }
  }

  assert {
    condition     = output.vpn_gateway.sku == "VpnGw1AZ"
    error_message = "vpn_gateway.sku should default to VpnGw1AZ"
  }

  assert {
    condition     = length(azurerm_local_network_gateway.sites) == 1
    error_message = "One local network gateway should exist for one site"
  }
}
