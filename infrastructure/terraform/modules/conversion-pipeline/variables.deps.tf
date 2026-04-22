/**
 * # Dependency Variables
 *
 * Resources provided by the platform module as typed object dependencies.
 */

variable "virtual_network" {
  type = object({
    id   = string
    name = string
  })
  description = "Virtual network from the platform module"
}

variable "subnets" {
  type = object({
    private_endpoints = object({
      id   = string
      name = string
    })
  })
  description = "Subnets from the platform module. Only the private endpoints subnet is consumed"
}

variable "private_dns_zones" {
  type = object({
    storage_blob = object({
      id   = string
      name = string
    })
    storage_dfs = object({
      id   = string
      name = string
    })
  })
  description = "Private DNS zones from the platform module. storage_blob and storage_dfs are required when private endpoints are enabled"
}

variable "log_analytics_workspace" {
  type = object({
    id           = string
    workspace_id = string
  })
  description = "Log Analytics workspace from the platform module. Used by diagnostic settings"
}
