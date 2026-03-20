// DNS standalone resource naming tests
// Validates VNet link name format and DNS zone/record names

mock_provider "azurerm" {}

override_data {
  target = data.azurerm_virtual_network.this
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001"
    name = "vnet-test-dev-001"
  }
}

// ============================================================
// VNet Link Name Format
// ============================================================

run "vnet_link_name" {
  command = plan

  variables {
    resource_prefix      = "test"
    environment          = "dev"
    instance             = "001"
    osmo_loadbalancer_ip = "10.0.1.100"
  }

  assert {
    condition     = azurerm_private_dns_zone_virtual_network_link.osmo.name == "vnet-pzl-osmo-test-dev-001"
    error_message = "VNet link name must follow vnet-pzl-osmo-{prefix}-{env}-{instance}"
  }
}

// ============================================================
// DNS Zone and Record Names
// ============================================================

run "dns_zone_and_record_names" {
  command = plan

  variables {
    resource_prefix      = "test"
    environment          = "dev"
    instance             = "001"
    osmo_loadbalancer_ip = "10.0.1.100"
  }

  assert {
    condition     = azurerm_private_dns_zone.osmo.name == "osmo.local"
    error_message = "DNS zone name must match osmo_private_dns_zone_name default"
  }

  assert {
    condition     = azurerm_private_dns_a_record.osmo.name == "dev"
    error_message = "A record name must match osmo_hostname default"
  }

  assert {
    condition     = contains(azurerm_private_dns_a_record.osmo.records, "10.0.1.100")
    error_message = "A record must contain the load balancer IP"
  }
}
