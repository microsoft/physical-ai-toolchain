/**
 * # Azure Automation Module
 *
 * Creates an Azure Automation Account with a scheduled PowerShell runbook
 * for automated startup of AKS clusters and PostgreSQL servers.
 */
locals {
  automation_account_name = "aa-${var.resource_prefix}-${var.environment}-${var.instance}"
  runbook_name            = "Start-AzureResources"
  schedule_name           = "morning-startup"
  location                = coalesce(var.location, var.resource_group.location)

  # Construct schedule start_time: tomorrow at the configured time (RFC3339 format)
  tomorrow_date = formatdate("YYYY-MM-DD", timeadd(timestamp(), "24h"))
  schedule_time = "${local.tomorrow_date}T${var.schedule_config.start_time}:00+00:00"
}

// ============================================================
// Automation Account
// ============================================================

resource "azurerm_automation_account" "this" {
  name                = local.automation_account_name
  location            = local.location
  resource_group_name = var.resource_group.name
  sku_name            = "Basic"

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

// ============================================================
// PowerShell 7.2 Modules
// ============================================================

resource "azurerm_automation_powershell72_module" "az_accounts" {
  name                  = "Az.Accounts"
  automation_account_id = azurerm_automation_account.this.id

  module_link {
    uri = "https://www.powershellgallery.com/api/v2/package/Az.Accounts/5.2.0"
  }

  tags = var.tags
}

resource "azurerm_automation_powershell72_module" "az_postgresql" {
  name                  = "Az.PostgreSql"
  automation_account_id = azurerm_automation_account.this.id

  module_link {
    uri = "https://www.powershellgallery.com/api/v2/package/Az.PostgreSql/1.4.0"
  }

  tags = var.tags

  depends_on = [azurerm_automation_powershell72_module.az_accounts]
}

// ============================================================
// Runbook
// ============================================================

resource "azurerm_automation_runbook" "start_resources" {
  name                    = local.runbook_name
  location                = local.location
  resource_group_name     = var.resource_group.name
  automation_account_name = azurerm_automation_account.this.name
  runbook_type            = "PowerShell72"
  log_verbose             = true
  log_progress            = true
  description             = "Starts PostgreSQL and AKS cluster for morning operations"

  content = file(var.runbook_script_path)

  tags = var.tags
}

// ============================================================
// Schedule
// ============================================================

resource "azurerm_automation_schedule" "morning_startup" {
  name                    = local.schedule_name
  resource_group_name     = var.resource_group.name
  automation_account_name = azurerm_automation_account.this.name
  frequency               = "Week"
  interval                = 1
  timezone                = var.schedule_config.timezone
  start_time              = local.schedule_time
  week_days               = var.schedule_config.week_days
  description             = "Start AKS and PostgreSQL every morning at ${var.schedule_config.start_time} ${var.schedule_config.timezone}"

  lifecycle {
    ignore_changes = [start_time]
  }
}

// ============================================================
// Job Schedule
// ============================================================

resource "azurerm_automation_job_schedule" "start_resources" {
  resource_group_name     = var.resource_group.name
  automation_account_name = azurerm_automation_account.this.name
  schedule_name           = azurerm_automation_schedule.morning_startup.name
  runbook_name            = azurerm_automation_runbook.start_resources.name

  // Parameter keys MUST be lowercase (Azure API normalization)
  parameters = {
    resourcegroupname  = var.resource_group.name
    postgresservername = var.postgresql_server != null ? var.postgresql_server.name : ""
    aksclustername     = var.aks_cluster.name
  }
}

// ============================================================
// RBAC Assignments
// ============================================================

resource "azurerm_role_assignment" "aks_contributor" {
  scope                            = var.aks_cluster.id
  role_definition_name             = "Azure Kubernetes Service Contributor Role"
  principal_id                     = azurerm_automation_account.this.identity[0].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "postgresql_contributor" {
  count = var.postgresql_server != null ? 1 : 0

  scope                            = var.postgresql_server.id
  role_definition_name             = "Contributor"
  principal_id                     = azurerm_automation_account.this.identity[0].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
