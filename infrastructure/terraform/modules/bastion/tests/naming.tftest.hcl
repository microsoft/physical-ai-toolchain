// Bastion module naming convention tests
// Validates resource names follow {abbreviation}-{prefix}-{env}-{instance} convention

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

run "verify_resource_naming" {
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
    condition     = azurerm_subnet.bastion.name == "AzureBastionSubnet"
    error_message = "Bastion subnet must use the fixed Azure name AzureBastionSubnet"
  }

  assert {
    condition     = azurerm_network_security_group.bastion[0].name == "nsg-bas-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Bastion NSG name must follow nsg-bas-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = azurerm_public_ip.bastion.name == "pip-bas-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Bastion public IP name must follow pip-bas-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = azurerm_bastion_host.main.name == "bas-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Bastion host name must follow bas-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = azurerm_network_interface.jumpbox[0].name == "nic-jb-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Jumpbox NIC name must follow nic-jb-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].name == "vm-jb-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Jumpbox VM name must follow vm-jb-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].os_disk[0].name == "osdisk-jb-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Jumpbox OS disk name must follow osdisk-jb-{prefix}-{env}-{instance}"
  }
}
