// Bastion module variable validation tests
// Validates invalid CIDRs, SKUs, scale units, SSH keys, and conditional jumpbox requirements

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

run "reject_invalid_cidr" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    should_deploy_jumpbox         = false
    bastion_subnet_address_prefix = "not-a-cidr"
  }

  expect_failures = [var.bastion_subnet_address_prefix]
}

run "reject_subnet_smaller_than_slash_26" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    resource_group                = run.setup.resource_group
    virtual_network               = run.setup.virtual_network
    should_deploy_jumpbox         = false
    bastion_subnet_address_prefix = "10.0.10.0/27"
  }

  expect_failures = [var.bastion_subnet_address_prefix]
}

run "reject_basic_bastion_sku" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    virtual_network       = run.setup.virtual_network
    should_deploy_jumpbox = false
    bastion_config = {
      sku = "Basic"
    }
  }

  expect_failures = [var.bastion_config]
}

run "reject_invalid_scale_units" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    virtual_network       = run.setup.virtual_network
    should_deploy_jumpbox = false
    bastion_config = {
      scale_units = 1
    }
  }

  expect_failures = [var.bastion_config]
}

run "reject_invalid_ssh_public_key" {
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
      admin_ssh_public_key = "not-a-valid-ssh-key"
    }
  }

  expect_failures = [var.jumpbox_config]
}

run "reject_missing_ssh_key_when_jumpbox_enabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    virtual_network       = run.setup.virtual_network
    jumpbox_subnet        = run.setup.jumpbox_subnet
    should_deploy_jumpbox = true
    jumpbox_config        = {}
  }

  expect_failures = [var.jumpbox_config]
}

run "reject_missing_jumpbox_subnet_when_jumpbox_enabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    virtual_network       = run.setup.virtual_network
    jumpbox_subnet        = null
    should_deploy_jumpbox = true
    jumpbox_config = {
      admin_ssh_public_key = run.setup.test_ssh_public_key
    }
  }

  expect_failures = [var.jumpbox_subnet]
}

run "allow_null_ssh_key_when_jumpbox_disabled" {
  command = plan

  variables {
    resource_prefix       = run.setup.resource_prefix
    environment           = run.setup.environment
    instance              = run.setup.instance
    location              = run.setup.location
    resource_group        = run.setup.resource_group
    virtual_network       = run.setup.virtual_network
    jumpbox_subnet        = null
    should_deploy_jumpbox = false
    jumpbox_config = {
      admin_ssh_public_key = null
    }
  }

  assert {
    condition     = length(azurerm_linux_virtual_machine.jumpbox) == 0
    error_message = "Bastion-only deployment should succeed without an SSH public key"
  }
}
