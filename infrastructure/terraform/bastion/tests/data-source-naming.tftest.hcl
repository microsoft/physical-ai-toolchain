// Bastion standalone data source naming tests
// Validates default name derivation and explicit name overrides for data source lookups

mock_provider "azurerm" {}

override_data {
  target = module.bastion.data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
    object_id = "00000000-0000-0000-0000-000000000000"
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

override_data {
  target = data.azurerm_subnet.jumpbox[0]
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001/subnets/snet-test-dev-001"
    name = "snet-test-dev-001"
  }
}

// ============================================================
// Default Name Derivation
// ============================================================

run "default_name_derivation" {
  command = plan

  variables {
    resource_prefix = "test"
    environment     = "dev"
    instance        = "001"
    location        = "westus3"
    jumpbox_config = {
      admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockTestKeyForTerraformPlanValidationOnly test@example.com"
    }
  }

  assert {
    condition     = data.azurerm_resource_group.this.name == "rg-test-dev-001"
    error_message = "Resource group should use default name rg-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = data.azurerm_virtual_network.this.name == "vnet-test-dev-001"
    error_message = "Virtual network should use default name vnet-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = data.azurerm_subnet.jumpbox[0].name == "snet-test-dev-001"
    error_message = "Jumpbox subnet should use default name snet-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = output.connection_info.bastion_name == "bas-test-dev-001"
    error_message = "Bastion name should follow bas-{prefix}-{env}-{instance}"
  }
}

// ============================================================
// Explicit Name Override
// ============================================================

run "name_override" {
  command = plan

  override_data {
    target = data.azurerm_resource_group.this
    values = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/custom-rg"
      name     = "custom-rg"
      location = "eastus2"
    }
  }

  override_data {
    target = data.azurerm_virtual_network.this
    values = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/custom-rg/providers/Microsoft.Network/virtualNetworks/custom-vnet"
      name = "custom-vnet"
    }
  }

  override_data {
    target = data.azurerm_subnet.jumpbox[0]
    values = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/custom-rg/providers/Microsoft.Network/virtualNetworks/custom-vnet/subnets/custom-snet"
      name = "custom-snet"
    }
  }

  variables {
    resource_prefix      = "test"
    environment          = "dev"
    instance             = "001"
    location             = "eastus2"
    resource_group_name  = "custom-rg"
    virtual_network_name = "custom-vnet"
    jumpbox_subnet_name  = "custom-snet"
    jumpbox_config = {
      admin_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockTestKeyForTerraformPlanValidationOnly test@example.com"
    }
  }

  assert {
    condition     = data.azurerm_resource_group.this.name == "custom-rg"
    error_message = "Resource group should use explicit override name"
  }

  assert {
    condition     = data.azurerm_virtual_network.this.name == "custom-vnet"
    error_message = "Virtual network should use explicit override name"
  }

  assert {
    condition     = data.azurerm_subnet.jumpbox[0].name == "custom-snet"
    error_message = "Jumpbox subnet should use explicit override name"
  }
}
