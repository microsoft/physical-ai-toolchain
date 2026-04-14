/**
 * # PostgreSQL Resources (Optional OSMO Service)
 *
 * This file creates the optional PostgreSQL Flexible Server for OSMO including:
 * - PostgreSQL Flexible Server with TimescaleDB extension
 * - Private endpoint for secure connectivity (supports cross-region deployment)
 * - Database definitions per configuration
 * - Password stored securely in Key Vault
 */

// ============================================================
// PostgreSQL Admin Password
// ============================================================

resource "random_password" "postgresql" {
  count = var.should_deploy_postgresql ? 1 : 0

  length           = 32
  special          = true
  override_special = "!@#$%&*()-_=+[]{}|;:,.<>?"
  min_lower        = 4
  min_upper        = 4
  min_numeric      = 4
  min_special      = 2
}

resource "azapi_resource" "postgresql_password" {
  count = var.should_deploy_postgresql ? 1 : 0

  type      = "Microsoft.KeyVault/vaults/secrets@2025-05-01"
  name      = "psql-admin-password"
  parent_id = azurerm_key_vault.main.id

  body = {
    properties = {
      value = random_password.postgresql[0].result
    }
  }

  depends_on = [azurerm_role_assignment.user_kv_officer]
}

// ============================================================
// PostgreSQL Flexible Server
// ============================================================

resource "azurerm_postgresql_flexible_server" "main" {
  count = var.should_deploy_postgresql ? 1 : 0

  name                          = "psql-${local.resource_name_suffix}"
  location                      = var.postgresql_config.location
  resource_group_name           = var.resource_group.name
  version                       = var.postgresql_config.version
  sku_name                      = var.postgresql_config.sku_name
  storage_mb                    = var.postgresql_config.storage_mb
  administrator_login           = "psqladmin"
  administrator_password        = random_password.postgresql[0].result
  zone                          = var.postgresql_config.zone
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = var.should_enable_public_network_access

  dynamic "high_availability" {
    for_each = var.postgresql_config.should_enable_high_availability ? [1] : []
    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = var.postgresql_config.standby_availability_zone
    }
  }

  lifecycle {
    ignore_changes = [zone]
  }
}

// ============================================================
// PostgreSQL Firewall Rules (public access without private endpoints)
// ============================================================

resource "azurerm_postgresql_flexible_server_firewall_rule" "aks_egress" {
  count = var.should_deploy_postgresql && var.should_enable_public_network_access && !local.pe_enabled && var.should_enable_nat_gateway ? 1 : 0

  name             = "aks-nat-gateway-egress"
  server_id        = azurerm_postgresql_flexible_server.main[0].id
  start_ip_address = azurerm_public_ip.nat_gateway[0].ip_address
  end_ip_address   = azurerm_public_ip.nat_gateway[0].ip_address
}

// ============================================================
// PostgreSQL Private Endpoint
// ============================================================

resource "azurerm_private_endpoint" "postgresql" {
  count = var.should_deploy_postgresql && local.pe_enabled ? 1 : 0

  name                = "pe-psql-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-psql-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_postgresql_flexible_server.main[0].id
    subresource_names              = ["postgresqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-psql-${local.resource_name_suffix}"
    private_dns_zone_ids = [azurerm_private_dns_zone.postgresql[0].id]
  }
}

// ============================================================
// PostgreSQL Configuration - Required Extensions
// ============================================================

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  count = var.should_deploy_postgresql ? 1 : 0

  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main[0].id
  value     = "HSTORE,UUID-OSSP,PG_STAT_STATEMENTS"

  depends_on = [azurerm_private_endpoint.postgresql]
}

// ============================================================
// PostgreSQL Databases
// ============================================================

resource "azurerm_postgresql_flexible_server_database" "databases" {
  for_each = var.should_deploy_postgresql ? var.postgresql_config.databases : {}

  name      = each.key
  server_id = azurerm_postgresql_flexible_server.main[0].id
  collation = each.value.collation
  charset   = each.value.charset

  depends_on = [azurerm_postgresql_flexible_server_configuration.extensions]
}
