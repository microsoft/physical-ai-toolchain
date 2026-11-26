// Bastion module default value tests
// Validates default CIDR, Bastion Standard tunneling SKU, and jumpbox defaults

mock_provider "azurerm" {}

override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
    object_id = "00000000-0000-0000-0000-000000000000"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "verify_defaults" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    virtual_network = run.setup.virtual_network
    jumpbox_subnet  = run.setup.jumpbox_subnet
    jumpbox_config = {
      admin_ssh_public_key = run.setup.test_ssh_public_key
    }
  }

  assert {
    condition     = azurerm_subnet.bastion.address_prefixes[0] == "10.0.10.0/26"
    error_message = "Default AzureBastionSubnet prefix must be 10.0.10.0/26 to avoid overlapping platform subnets"
  }

  assert {
    condition     = azurerm_bastion_host.main.sku == "Standard"
    error_message = "Default Bastion SKU must be Standard"
  }

  assert {
    condition     = azurerm_bastion_host.main.scale_units == 2
    error_message = "Default Bastion scale_units must be 2"
  }

  assert {
    condition     = azurerm_bastion_host.main.tunneling_enabled == true
    error_message = "Default Bastion tunneling_enabled must be true"
  }

  assert {
    condition     = azurerm_public_ip.bastion.sku == "Standard" && azurerm_public_ip.bastion.allocation_method == "Static"
    error_message = "Bastion public IP must use Standard SKU with Static allocation"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].size == "Standard_B2s"
    error_message = "Default jumpbox VM size must be Standard_B2s"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].admin_username == "azureuser"
    error_message = "Default jumpbox admin username must be azureuser"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].os_disk[0].disk_size_gb == 64
    error_message = "Default jumpbox OS disk size must be 64 GB"
  }
}
