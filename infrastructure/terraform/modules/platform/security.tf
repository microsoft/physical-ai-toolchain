/**
 * # Security Resources
 *
 * This file creates security infrastructure for the Platform module including:
 * - Key Vault for secrets management with RBAC authorization
 * - User Assigned Managed Identity for ML workloads
 * - Role assignments for Key Vault access
 * - Private endpoint for Key Vault (when PE enabled)
 */

// ============================================================
// Key Vault
// ============================================================

resource "azurerm_key_vault" "main" {
  name                          = "kv${var.resource_prefix}${var.environment}${var.instance}"
  location                      = var.resource_group.location
  resource_group_name           = var.resource_group.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  purge_protection_enabled      = var.should_enable_purge_protection
  public_network_access_enabled = var.should_enable_public_network_access

  network_acls {
    bypass = "AzureServices"
    // Allow public access when enabled, otherwise deny (PE-only)
    default_action = var.should_enable_public_network_access ? "Allow" : "Deny"
  }

  lifecycle {
    prevent_destroy = true
  }
}

// ============================================================
// User Assigned Managed Identity for ML Workloads
// ============================================================

resource "azurerm_user_assigned_identity" "ml" {
  name                = "id-ml-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
}

// ============================================================
// User Assigned Managed Identity for OSMO Workloads
// ============================================================

resource "azurerm_user_assigned_identity" "osmo" {
  count               = var.should_enable_osmo_identity ? 1 : 0
  name                = "id-osmo-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
}

// ============================================================
// OSMO Admin Password
// ============================================================

resource "random_password" "osmo_admin" {
  count = local.should_deploy_osmo ? 1 : 0

  length      = 43
  special     = false
  min_lower   = 4
  min_upper   = 4
  min_numeric = 4
}

resource "azapi_resource" "osmo_admin_password" {
  count = local.should_deploy_osmo ? 1 : 0

  type      = "Microsoft.KeyVault/vaults/secrets@2025-05-01"
  name      = "osmo-admin-password"
  parent_id = azurerm_key_vault.main.id

  body = {
    properties = {
      value = random_password.osmo_admin[0].result
    }
  }

  depends_on = [azurerm_role_assignment.user_kv_officer]
}

// ============================================================
// Key Vault Private Endpoint
// ============================================================

resource "azurerm_private_endpoint" "key_vault" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-kv-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-kv-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_key_vault.main.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-kv-${local.resource_name_suffix}"
    private_dns_zone_ids = [azurerm_private_dns_zone.core["key_vault"].id]
  }
}
