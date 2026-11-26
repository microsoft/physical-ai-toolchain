// Bastion module output structure and nullability tests

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

run "outputs_with_all_components" {
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
    condition     = output.bastion_subnet.name == "AzureBastionSubnet"
    error_message = "bastion_subnet.name must be AzureBastionSubnet"
  }

  assert {
    condition     = output.bastion_host.sku == "Standard" && output.bastion_host.tunneling_enabled == true
    error_message = "bastion_host output must report Standard SKU and tunneling_enabled = true"
  }

  assert {
    condition     = output.bastion_nsg != null
    error_message = "bastion_nsg output must be populated when should_create_bastion_nsg is true"
  }

  assert {
    condition     = output.jumpbox != null && output.jumpbox.admin_username == "azureuser"
    error_message = "jumpbox output must be populated when should_deploy_jumpbox is true"
  }

  assert {
    condition     = output.connection_info.bastion_name == "bas-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "connection_info.bastion_name must match Bastion host name"
  }

  assert {
    condition     = output.connection_info.jumpbox_name == "vm-jb-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "connection_info.jumpbox_name must match jumpbox VM name"
  }
}

run "outputs_when_optional_components_disabled" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    virtual_network           = run.setup.virtual_network
    should_create_bastion_nsg = false
    should_deploy_jumpbox     = false
    jumpbox_subnet            = null
  }

  assert {
    condition     = output.bastion_nsg == null
    error_message = "bastion_nsg output must be null when should_create_bastion_nsg is false"
  }

  assert {
    condition     = output.jumpbox == null
    error_message = "jumpbox output must be null when should_deploy_jumpbox is false"
  }

  assert {
    condition     = output.connection_info.jumpbox_id == null && output.connection_info.jumpbox_name == null
    error_message = "connection_info jumpbox fields must be null when should_deploy_jumpbox is false"
  }
}
