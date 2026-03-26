// DNS standalone output tests
// Validates FQDN format matches "{hostname}.{zone_name}"

mock_provider "azurerm" {}

override_data {
  target = data.azurerm_virtual_network.this
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001"
    name = "vnet-test-dev-001"
  }
}

// ============================================================
// FQDN Output Format
// ============================================================

run "fqdn_format" {
  command = plan

  variables {
    resource_prefix      = "test"
    environment          = "dev"
    instance             = "001"
    osmo_loadbalancer_ip = "10.0.1.100"
  }

  assert {
    condition     = output.osmo_fqdn == "dev.osmo.local"
    error_message = "FQDN must follow {hostname}.{zone_name} format"
  }
}
