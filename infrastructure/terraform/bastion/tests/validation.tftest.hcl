// Bastion standalone variable validation tests
// Validates CIDR, SKU, scale units, and conditional jumpbox SSH key requirements

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

run "reject_smaller_than_slash_26" {
  command = plan

  variables {
    resource_prefix               = "test"
    environment                   = "dev"
    instance                      = "001"
    location                      = "westus3"
    should_deploy_jumpbox         = false
    bastion_subnet_address_prefix = "10.0.10.0/27"
  }

  expect_failures = [var.bastion_subnet_address_prefix]
}

run "reject_basic_sku" {
  command = plan

  variables {
    resource_prefix       = "test"
    environment           = "dev"
    instance              = "001"
    location              = "westus3"
    should_deploy_jumpbox = false
    bastion_config = {
      sku = "Basic"
    }
  }

  expect_failures = [var.bastion_config]
}

run "reject_missing_ssh_key_when_jumpbox_enabled" {
  command = plan

  variables {
    resource_prefix       = "test"
    environment           = "dev"
    instance              = "001"
    location              = "westus3"
    should_deploy_jumpbox = true
    jumpbox_config        = {}
  }

  expect_failures = [var.jumpbox_config]
}

run "allow_bastion_only_without_ssh_key" {
  command = plan

  variables {
    resource_prefix       = "test"
    environment           = "dev"
    instance              = "001"
    location              = "westus3"
    should_deploy_jumpbox = false
    jumpbox_config = {
      admin_ssh_public_key = null
    }
  }

  assert {
    condition     = output.jumpbox == null && output.bastion_host.sku == "Standard"
    error_message = "Bastion-only deployment should succeed without an SSH key and return null jumpbox output"
  }
}
