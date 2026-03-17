/**
 * # Automation Module Outputs
 *
 * Typed object outputs for consumption by other modules.
 */

/*
 * Automation Account Outputs
 */

output "automation_account" {
  description = "Automation account resource details"
  value = {
    id           = azurerm_automation_account.this.id
    name         = azurerm_automation_account.this.name
    principal_id = azurerm_automation_account.this.identity[0].principal_id
  }
}

/*
 * Runbook Outputs
 */

output "runbook" {
  description = "Runbook resource details"
  value = {
    name = azurerm_automation_runbook.start_resources.name
  }
}

/*
 * Schedule Outputs
 */

output "schedule" {
  description = "Schedule resource details"
  value = {
    name      = azurerm_automation_schedule.morning_startup.name
    week_days = azurerm_automation_schedule.morning_startup.week_days
    timezone  = azurerm_automation_schedule.morning_startup.timezone
  }
}
