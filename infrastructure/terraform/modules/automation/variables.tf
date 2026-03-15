/**
 * # Automation Module Variables
 *
 * Module-specific variables for Azure Automation account and scheduled runbook.
 */

/*
 * Resource Dependencies - Required
 */

variable "aks_cluster" {
  type = object({
    id   = string
    name = string
  })
  description = "AKS cluster object containing id and name for startup and RBAC assignment"
}

variable "runbook_script_path" {
  type        = string
  description = "Path to PowerShell runbook script file"
}

/*
 * Resource Dependencies - Optional
 */

variable "postgresql_server" {
  type = object({
    id   = string
    name = string
  })
  description = "PostgreSQL server object containing id and name for startup and RBAC assignment (null to skip)"
  default     = null
}

/*
 * Schedule Configuration - Optional
 */

variable "schedule_config" {
  type = object({
    start_time = string
    week_days  = list(string)
    timezone   = string
  })
  description = "Schedule configuration for startup runbook including start time (HH:MM), week days, and timezone"
  default = {
    start_time = "08:00"
    week_days  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    timezone   = "UTC"
  }
}
