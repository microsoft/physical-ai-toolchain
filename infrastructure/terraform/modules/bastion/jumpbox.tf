/**
 * # Private Linux Jumpbox Resources
 *
 * Deploys an optional private Linux jumpbox VM in the platform subnet with
 * no public IP, Trusted Launch, System-Assigned Managed Identity, and Entra ID SSH login.
 */

// ============================================================
// Jumpbox Network Interface (Private IP Only)
// ============================================================

resource "azurerm_network_interface" "jumpbox" {
  count = var.should_deploy_jumpbox ? 1 : 0

  name                = "nic-jb-${local.resource_name_suffix}"
  location            = var.location
  resource_group_name = var.resource_group.name
  tags                = local.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = try(var.jumpbox_subnet.id, null)
    private_ip_address_allocation = "Dynamic"
  }

  lifecycle {
    precondition {
      condition     = var.jumpbox_subnet != null
      error_message = "jumpbox_subnet must be provided when should_deploy_jumpbox is true."
    }
  }
}

// ============================================================
// Private Linux Jumpbox Virtual Machine
// ============================================================

resource "azurerm_linux_virtual_machine" "jumpbox" {
  # checkov:skip=CKV_AZURE_50:Entra ID SSH login requires the Microsoft AADSSHLoginForLinux VM extension.
  count = var.should_deploy_jumpbox ? 1 : 0

  name                            = "vm-jb-${local.resource_name_suffix}"
  resource_group_name             = var.resource_group.name
  location                        = var.location
  size                            = var.jumpbox_config.vm_size
  admin_username                  = var.jumpbox_config.admin_username
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.jumpbox[0].id]
  zone                            = var.jumpbox_config.zone
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  provision_vm_agent              = true
  custom_data                     = local.jumpbox_cloud_init != null ? base64encode(local.jumpbox_cloud_init) : null
  tags                            = local.tags

  identity {
    type = "SystemAssigned"
  }

  dynamic "admin_ssh_key" {
    for_each = var.jumpbox_config.admin_ssh_public_key != null ? [1] : []
    content {
      username   = var.jumpbox_config.admin_username
      public_key = trimspace(var.jumpbox_config.admin_ssh_public_key)
    }
  }

  os_disk {
    name                 = "osdisk-jb-${local.resource_name_suffix}"
    caching              = "ReadWrite"
    storage_account_type = var.jumpbox_config.os_disk_storage_account_type
    disk_size_gb         = var.jumpbox_config.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }

  lifecycle {
    precondition {
      condition     = var.jumpbox_config.admin_ssh_public_key != null && trimspace(var.jumpbox_config.admin_ssh_public_key) != ""
      error_message = "jumpbox_config.admin_ssh_public_key must be provided when should_deploy_jumpbox is true."
    }
  }
}

// ============================================================
// Entra ID SSH Login Extension
// ============================================================

resource "azurerm_virtual_machine_extension" "aad_ssh_login" {
  count = var.should_deploy_jumpbox ? 1 : 0

  name                       = "AADSSHLoginForLinux"
  virtual_machine_id         = azurerm_linux_virtual_machine.jumpbox[0].id
  publisher                  = "Microsoft.Azure.ActiveDirectory"
  type                       = "AADSSHLoginForLinux"
  type_handler_version       = "1.0"
  auto_upgrade_minor_version = true
  tags                       = local.tags
}
