/**
 * # Dependency Variables
 *
 * Variables for resources provided by the Platform module as typed object dependencies.
 */

/*
 * Dependencies from Platform Module
 */

variable "virtual_network" {
  type = object({
    id   = string
    name = string
  })
  description = "Virtual network from platform module"
}

variable "network_security_group" {
  type = object({
    id = string
  })
  description = "NSG from platform module"
}

variable "nat_gateway" {
  type = object({
    id = string
  })
  description = "NAT Gateway from platform module. Null when NAT Gateway is disabled"
  default     = null
}

variable "log_analytics_workspace" {
  type = object({
    id           = string
    workspace_id = string
  })
  description = "Log Analytics workspace from platform module"
}

variable "container_registry" {
  type = object({
    id           = string
    name         = string
    login_server = string
  })
  description = "ACR from platform module"
}

variable "storage_account" {
  type = object({
    id   = string
    name = string
  })
  description = "Storage account from platform module"
}
