/**
 * # Automation Deployment Outputs
 *
 * Outputs from standalone automation deployment.
 */

/*
 * Automation Account Outputs
 */

output "automation_account" {
  description = "Automation account resource details including id, name, and principal_id"
  value       = module.automation.automation_account
}

/*
 * Runbook Outputs
 */

output "runbook" {
  description = "Runbook resource details"
  value       = module.automation.runbook
}

/*
 * Schedule Outputs
 */

output "schedule" {
  description = "Schedule resource details including name, week_days, and timezone"
  value       = module.automation.schedule
}
