/**
 * # Core Variables
 *
 * Standard variables consistent across all modules: environment, resource_prefix,
 * instance, resource_group, and an optional location override.
 */

variable "environment" {
  type        = string
  description = "Environment for all resources in this module: dev, staging, or prod"
}

variable "resource_prefix" {
  type        = string
  description = "Prefix for all resources in this module"
}

variable "instance" {
  type        = string
  description = "Instance identifier for naming resources: 001, 002, etc"
  default     = "001"
}

variable "resource_group" {
  type = object({
    id       = string
    name     = string
    location = string
  })
  description = "Resource group object containing name, id, and location"
}

variable "location" {
  type        = string
  description = "Override location for module resources. Defaults to var.resource_group.location when null"
  default     = null
}
