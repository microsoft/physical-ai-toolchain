// VPN module site-to-site naming tests
// Validates site slug generation and connection resource naming

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
// Site Slug Generation
// ============================================================

run "site_slug_from_display_name" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    vpn_site_connections = [{
      name                 = "Lab Network"
      address_spaces       = ["10.1.0.0/16"]
      shared_key_reference = "lab_key"
      gateway_ip_address   = "1.2.3.4"
    }]
    vpn_site_shared_keys = {
      lab_key = "dummyKey123"
    }
  }

  // Slug: "Lab Network" → "labnetwork"
  assert {
    condition     = azurerm_local_network_gateway.sites["Lab Network"].name == "lgw-labnetwork-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Local network gateway name should use slugified site name"
  }

  assert {
    condition     = azurerm_virtual_network_gateway_connection.sites["Lab Network"].name == "vcn-labnetwork-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "VPN connection name should use slugified site name"
  }
}

run "site_slug_with_special_chars" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    vpn_site_connections = [{
      name                 = "Remote-Office-1"
      address_spaces       = ["10.2.0.0/16"]
      shared_key_reference = "office_key"
      gateway_ip_address   = "5.6.7.8"
    }]
    vpn_site_shared_keys = {
      office_key = "dummyKey456"
    }
  }

  // Slug: "Remote-Office-1" → "remoteoffice1"
  assert {
    condition     = azurerm_local_network_gateway.sites["Remote-Office-1"].name == "lgw-remoteoffice1-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Local network gateway name should strip special characters from site name"
  }

  assert {
    condition     = azurerm_virtual_network_gateway_connection.sites["Remote-Office-1"].name == "vcn-remoteoffice1-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "VPN connection name should strip special characters from site name"
  }
}
