// VPN module conditional resource tests
// Validates authentication modes and S2S connection conditionals

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
// AAD Authentication Only
// ============================================================

run "aad_auth_only" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    aad_auth_config = {
      should_enable = true
    }
    root_certificate_public_data = null
  }

  assert {
    condition     = contains(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].vpn_auth_types, "AAD")
    error_message = "AAD auth type should be present when AAD auth is enabled"
  }

  assert {
    condition     = length(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].root_certificate) == 0
    error_message = "Root certificate should not be present when certificate auth is disabled"
  }

  assert {
    condition     = azurerm_virtual_network_gateway.main.vpn_client_configuration[0].aad_tenant == "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000"
    error_message = "AAD tenant URL should use the tenant ID from client config"
  }
}

// ============================================================
// Certificate Authentication Only
// ============================================================

run "certificate_auth_only" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    aad_auth_config = {
      should_enable = false
    }
    root_certificate_public_data = "MIICtest123base64encodedcertdata"
  }

  assert {
    condition     = contains(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].vpn_auth_types, "Certificate")
    error_message = "Certificate auth type should be present when certificate data is provided"
  }

  assert {
    condition     = length(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].root_certificate) == 1
    error_message = "Root certificate should be present when certificate auth is enabled"
  }

  assert {
    condition     = azurerm_virtual_network_gateway.main.vpn_client_configuration[0].aad_tenant == null
    error_message = "AAD tenant should be null when AAD auth is disabled"
  }

  assert {
    condition     = contains(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].vpn_client_protocols, "IkeV2")
    error_message = "IkeV2 protocol should be enabled when certificate auth is available"
  }
}

// ============================================================
// Both Authentication Types
// ============================================================

run "both_auth_types" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    aad_auth_config = {
      should_enable = true
    }
    root_certificate_public_data = "MIICtest123base64encodedcertdata"
  }

  assert {
    condition     = contains(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].vpn_auth_types, "AAD")
    error_message = "AAD auth type should be present when both auth types are enabled"
  }

  assert {
    condition     = contains(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].vpn_auth_types, "Certificate")
    error_message = "Certificate auth type should be present when both auth types are enabled"
  }

  assert {
    condition     = length(azurerm_virtual_network_gateway.main.vpn_client_configuration[0].root_certificate) == 1
    error_message = "Root certificate should be present when both auth types are enabled"
  }
}

// ============================================================
// Site-to-Site Conditionals
// ============================================================

run "s2s_connections_enabled" {
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
      shared_key_reference = "testsite_key"
      gateway_ip_address   = "1.2.3.4"
    }]
    vpn_site_shared_keys = {
      testsite_key = "dummySharedKey123"
    }
  }

  assert {
    condition     = length(azurerm_local_network_gateway.sites) == 1
    error_message = "Local network gateway should be created for each S2S site"
  }

  assert {
    condition     = length(azurerm_virtual_network_gateway_connection.sites) == 1
    error_message = "VPN connection should be created for each S2S site"
  }
}

run "s2s_connections_disabled" {
  command = plan

  variables {
    resource_prefix      = run.setup.resource_prefix
    environment          = run.setup.environment
    instance             = run.setup.instance
    location             = run.setup.location
    resource_group       = run.setup.resource_group
    virtual_network      = run.setup.virtual_network
    vpn_site_connections = []
    vpn_site_shared_keys = {}
  }

  assert {
    condition     = length(azurerm_local_network_gateway.sites) == 0
    error_message = "No local network gateways when S2S connections list is empty"
  }

  assert {
    condition     = length(azurerm_virtual_network_gateway_connection.sites) == 0
    error_message = "No VPN connections when S2S connections list is empty"
  }
}
