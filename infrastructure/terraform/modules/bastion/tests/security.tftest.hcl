// Bastion module security invariant tests
// Validates 8 mandatory Bastion NSG rules, Trusted Launch, SSH-only auth, SystemAssigned MI, and Entra extension

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

run "verify_security_posture" {
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
    condition     = length(azurerm_network_security_group.bastion[0].security_rule) == 8
    error_message = "Bastion NSG must include all 8 mandatory Microsoft security rules"
  }

  assert {
    condition     = contains([for r in azurerm_network_security_group.bastion[0].security_rule : r.name], "AllowHttpsInbound")
    error_message = "Bastion NSG must include AllowHttpsInbound"
  }

  assert {
    condition     = contains([for r in azurerm_network_security_group.bastion[0].security_rule : r.name], "AllowGatewayManagerInbound")
    error_message = "Bastion NSG must include AllowGatewayManagerInbound"
  }

  assert {
    condition     = contains([for r in azurerm_network_security_group.bastion[0].security_rule : r.name], "AllowSshRdpOutbound")
    error_message = "Bastion NSG must include AllowSshRdpOutbound"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].disable_password_authentication == true
    error_message = "Jumpbox must disable password authentication"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].secure_boot_enabled == true && azurerm_linux_virtual_machine.jumpbox[0].vtpm_enabled == true
    error_message = "Jumpbox must enable Trusted Launch (secure_boot_enabled and vtpm_enabled)"
  }

  assert {
    condition     = azurerm_linux_virtual_machine.jumpbox[0].identity[0].type == "SystemAssigned"
    error_message = "Jumpbox must enable SystemAssigned managed identity"
  }

  assert {
    condition     = azurerm_network_interface.jumpbox[0].ip_configuration[0].public_ip_address_id == null
    error_message = "Jumpbox NIC must remain private with no public IP"
  }

  assert {
    condition     = azurerm_virtual_machine_extension.aad_ssh_login[0].publisher == "Microsoft.Azure.ActiveDirectory" && azurerm_virtual_machine_extension.aad_ssh_login[0].type == "AADSSHLoginForLinux"
    error_message = "Jumpbox must install Microsoft.Azure.ActiveDirectory AADSSHLoginForLinux extension"
  }
}
