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

variable "subnets" {
  type = object({
    main = object({
      id   = string
      name = string
    })
    private_endpoints = optional(object({
      id   = string
      name = string
    }))
  })
  description = "Subnets from platform module. Private endpoints subnet is optional and only provided when private endpoints are enabled"
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
}

variable "log_analytics_workspace" {
  type = object({
    id           = string
    workspace_id = string
  })
  description = "Log Analytics from platform module"
}

variable "monitor_workspace" {
  type = object({
    id = string
  })
  description = "Azure Monitor workspace from platform module. Null when monitor workspace is disabled"
  default     = null
}

variable "data_collection_endpoint" {
  type = object({
    id = string
  })
  description = "Data Collection Endpoint from platform module. Null when DCE is disabled"
  default     = null
}

variable "should_deploy_monitor_workspace" {
  type        = bool
  description = "Whether Azure Monitor Workspace is enabled for AKS observability"
  default     = true
}

variable "should_deploy_dce" {
  type        = bool
  description = "Whether Data Collection Endpoint is enabled for AKS observability"
  default     = true
}

variable "container_registry" {
  type = object({
    id           = string
    name         = string
    login_server = string
  })
  description = "ACR from platform module"
}

variable "private_dns_zones" {
  type = map(object({
    id   = string
    name = string
  }))
  description = "Private DNS zones from platform module"
  default     = {}
}
