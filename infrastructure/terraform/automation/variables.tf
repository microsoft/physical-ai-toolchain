/**
 * # Automation Deployment Variables
 *
 * Input variables for standalone automation deployment.
 */

/*
 * Core Variables - Required
 */

variable "environment" {
  type        = string
  description = "Environment for all resources in this module: dev, test, or prod"
}

variable "location" {
  type        = string
  description = "Location for all resources in this module"
}

variable "resource_prefix" {
  type        = string
  description = "Prefix for all resources in this module"
}

/*
 * Core Variables - Optional
 */

variable "instance" {
  type        = string
  description = "Instance identifier for naming resources: 001, 002, etc"
  default     = "001"
}

variable "resource_group_name" {
  type        = string
  description = "Existing resource group name (Otherwise 'rg-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

/*
 * Resource Override Variables - Optional
 */

variable "aks_cluster_name" {
  type        = string
  description = "Override AKS cluster name (Otherwise 'aks-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

variable "postgresql_name" {
  type        = string
  description = "Override PostgreSQL server name (Otherwise 'psql-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

/*
 * Automation Configuration - Optional
 */

variable "schedule_config" {
  type = object({
    start_time = string
    week_days  = list(string)
    timezone   = string
  })
  description = "Schedule configuration for startup runbook including start time (HH:MM), week days, and timezone"
  default = {
    start_time = "13:00"
    week_days  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    timezone   = "Etc/UTC"
  }
}

variable "should_start_postgresql" {
  type        = bool
  description = "Whether to include PostgreSQL in the startup sequence"
  default     = true
}
