// VPN module naming convention tests
// Validates resource names follow {abbreviation}-{prefix}-{env}-{instance} convention

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

run "verify_resource_naming" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
  }

  // GatewaySubnet uses a fixed name required by Azure
  assert {
    condition     = azurerm_subnet.gateway.name == "GatewaySubnet"
    error_message = "Gateway subnet must use the fixed name GatewaySubnet"
  }

  // VPN Gateway Public IP
  assert {
    condition     = azurerm_public_ip.vpn_gateway.name == "pip-vgw-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "VPN Gateway public IP name must follow pip-vgw-{prefix}-{env}-{instance}"
  }

  // VPN Gateway
  assert {
    condition     = azurerm_virtual_network_gateway.main.name == "vgw-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "VPN Gateway name must follow vgw-{prefix}-{env}-{instance}"
  }
}
