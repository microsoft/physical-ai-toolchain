/**
 * # Core Variables
 * Core variables shared across all modules: environment, resource_prefix, location, instance.
 */

variable "environment" {
  description = "Deployment environment (dev, staging, prod)."
  type        = string
}

variable "instance" {
  description = "Instance identifier for the deployment (zero-padded, e.g. 001)."
  type        = string
  default     = "001"
}

// tflint-ignore: terraform_unused_declarations
variable "location" {
  description = "Azure region. Not used by this module (UAMI + federated credentials are global) but accepted for cross-module symmetry."
  type        = string
  default     = null
}

variable "resource_group" {
  description = "Resource group object the module deploys into."
  type = object({
    id       = string
    name     = string
    location = string
  })
}

variable "resource_prefix" {
  description = "Short prefix included in every resource name."
  type        = string
}
