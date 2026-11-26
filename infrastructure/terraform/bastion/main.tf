/**
 * # Azure Bastion Standalone Configuration
 *
 * Deploys Azure Bastion Standard and an optional private Entra-authenticated Linux jumpbox
 * using data sources to reference existing platform infrastructure.
 */

locals {
  resource_group_name  = coalesce(var.resource_group_name, "rg-${var.resource_prefix}-${var.environment}-${var.instance}")
  virtual_network_name = coalesce(var.virtual_network_name, "vnet-${var.resource_prefix}-${var.environment}-${var.instance}")
  jumpbox_subnet_name  = coalesce(var.jumpbox_subnet_name, "snet-${var.resource_prefix}-${var.environment}-${var.instance}")
}

data "azurerm_resource_group" "this" {
  name = local.resource_group_name
}

data "azurerm_virtual_network" "this" {
  name                = local.virtual_network_name
  resource_group_name = local.resource_group_name
}

data "azurerm_subnet" "jumpbox" {
  count = var.should_deploy_jumpbox ? 1 : 0

  name                 = local.jumpbox_subnet_name
  virtual_network_name = data.azurerm_virtual_network.this.name
  resource_group_name  = local.resource_group_name
}

// ============================================================
// Azure Bastion Module
// ============================================================

module "bastion" {
  source = "../modules/bastion"

  // Core variables
  environment     = var.environment
  resource_prefix = var.resource_prefix
  location        = var.location
  instance        = var.instance
  tags            = var.tags

  resource_group = {
    id       = data.azurerm_resource_group.this.id
    name     = data.azurerm_resource_group.this.name
    location = data.azurerm_resource_group.this.location
  }

  // Dependencies from data sources
  virtual_network = {
    id   = data.azurerm_virtual_network.this.id
    name = data.azurerm_virtual_network.this.name
  }

  jumpbox_subnet = var.should_deploy_jumpbox ? {
    id   = data.azurerm_subnet.jumpbox[0].id
    name = data.azurerm_subnet.jumpbox[0].name
  } : null

  // Optional least-privilege RBAC targets for jumpbox managed identity
  aks_cluster_id        = var.aks_cluster_id
  container_registry_id = var.container_registry_id
  key_vault_id          = var.key_vault_id
  storage_account_id    = var.storage_account_id

  // Bastion networking and host configuration
  bastion_subnet_address_prefix         = var.bastion_subnet_address_prefix
  should_create_bastion_nsg             = var.should_create_bastion_nsg
  bastion_allowed_source_address_prefix = var.bastion_allowed_source_address_prefix
  bastion_config                        = var.bastion_config

  // Jumpbox and Entra ID SSH login configuration
  should_deploy_jumpbox                  = var.should_deploy_jumpbox
  jumpbox_config                         = var.jumpbox_config
  should_assign_current_user_admin_login = var.should_assign_current_user_admin_login
  current_user_oid                       = var.current_user_oid
  jumpbox_admin_principal_ids            = var.jumpbox_admin_principal_ids
  jumpbox_user_principal_ids             = var.jumpbox_user_principal_ids
}
