/**
 * # Core Variables
 *
 * Core variables shared across all modules: environment, resource_prefix, location, instance.
 */

/*
 * Core Variables
 * Standard variables consistent across all modules
 */

variable "environment" {
  type        = string
  description = "Environment for all resources in this module: dev, test, or prod"
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

variable "resource_prefix" {
  type        = string
  description = "Prefix for all resources in this module"
}
