/**
 * # Azure Automation Standalone Configuration
 *
 * Deploys Azure Automation Account with scheduled runbook to start
 * AKS cluster and PostgreSQL server every morning.
 * Uses data sources to reference existing platform infrastructure.
 */
locals {
  resource_group_name = coalesce(var.resource_group_name, "rg-${var.resource_prefix}-${var.environment}-${var.instance}")
  aks_cluster_name    = coalesce(var.aks_cluster_name, "aks-${var.resource_prefix}-${var.environment}-${var.instance}")
  postgresql_name     = coalesce(var.postgresql_name, "psql-${var.resource_prefix}-${var.environment}-${var.instance}")
}

data "azurerm_resource_group" "this" {
  name = local.resource_group_name
}

data "azurerm_kubernetes_cluster" "this" {
  name                = local.aks_cluster_name
  resource_group_name = local.resource_group_name
}

data "azurerm_postgresql_flexible_server" "this" {
  count               = var.should_start_postgresql ? 1 : 0
  name                = local.postgresql_name
  resource_group_name = local.resource_group_name
}

// ============================================================
// Automation Module
// ============================================================

module "automation" {
  source = "../modules/automation"

  // Core variables
  environment     = var.environment
  resource_prefix = var.resource_prefix
  location        = var.location
  instance        = var.instance
  tags            = {}

  resource_group = data.azurerm_resource_group.this

  // Dependencies from data sources
  aks_cluster = data.azurerm_kubernetes_cluster.this

  postgresql_server = var.should_start_postgresql ? data.azurerm_postgresql_flexible_server.this[0] : null

  // Automation configuration
  schedule_config     = var.schedule_config
  runbook_script_path = "${path.module}/scripts/Start-AzureResources.ps1"
}
