/**
 * # SiL Module Variables
 *
 * Module-specific variables for Software-in-the-Loop (SiL) infrastructure
 * including AKS cluster, Azure ML extension, and container insights configuration.
 */

/*
 * Current User Configuration
 */

variable "current_user_oid" {
  type        = string
  description = "Object ID of the current user for cluster admin role assignments. Obtained via Microsoft Graph to avoid constant updates from azurerm_client_config"
  default     = null
}

/*
 * Private Endpoint Variables
 */

variable "should_enable_private_endpoint" {
  type        = bool
  description = "Whether to enable private endpoints for AKS cluster"
  default     = true
}

variable "should_enable_nat_gateway" {
  type        = bool
  description = "Whether NAT Gateway is enabled for outbound connectivity. When true, subnets disable default outbound access; when false, subnets use Azure default outbound access"
  default     = true
}

/*
 * Cluster Admin Configuration
 */

variable "should_assign_cluster_admin" {
  type        = bool
  description = "Whether to assign Azure Kubernetes Cluster Admin Role to the current user"
  default     = true
}

/*
 * AKS Networking Variables
 */

variable "aks_subnet_config" {
  type = object({
    subnet_address_prefix_aks = optional(string, "10.0.5.0/24")
  })
  description = "AKS subnet address configuration for system node pool. When properties are null, defaults are used. Note: Pod subnets are not used with Azure CNI Overlay mode"
  default     = {}
}

/*
 * AKS Cluster Variables
 */

variable "aks_config" {
  type = object({
    system_node_pool_vm_size                    = string
    system_node_pool_node_count                 = number
    should_enable_system_node_pool_auto_scaling = bool
    system_node_pool_min_count                  = optional(number)
    system_node_pool_max_count                  = optional(number)
    should_enable_private_cluster               = bool
    system_node_pool_zones                      = optional(list(string))
    should_enable_microsoft_defender            = optional(bool, false)
    sku_tier                                    = optional(string, "Standard")
    support_plan                                = optional(string, "KubernetesOfficial")
  })
  description = "AKS cluster configuration for the system node pool, SKU tier, and support plan. AKSLongTermSupport requires the Premium tier"
  default = {
    system_node_pool_vm_size                    = "Standard_D8ds_v5"
    system_node_pool_node_count                 = 2
    should_enable_system_node_pool_auto_scaling = false
    system_node_pool_min_count                  = null
    system_node_pool_max_count                  = null
    should_enable_private_cluster               = true
    system_node_pool_zones                      = null
  }

  validation {
    condition     = contains(["Free", "Standard", "Premium"], var.aks_config.sku_tier)
    error_message = "aks_config.sku_tier must be Free, Standard, or Premium."
  }

  validation {
    condition     = contains(["KubernetesOfficial", "AKSLongTermSupport"], var.aks_config.support_plan)
    error_message = "aks_config.support_plan must be KubernetesOfficial or AKSLongTermSupport."
  }

  validation {
    condition     = var.aks_config.support_plan != "AKSLongTermSupport" || var.aks_config.sku_tier == "Premium"
    error_message = "aks_config.support_plan AKSLongTermSupport requires aks_config.sku_tier Premium."
  }
}

variable "node_pools" {
  type = map(object({
    vm_size                    = string
    node_count                 = optional(number, null)
    subnet_address_prefixes    = optional(list(string), [])
    subnet_pool_key            = optional(string, null)
    node_taints                = optional(list(string), [])
    node_labels                = optional(map(string), {})
    gpu_driver                 = optional(string)
    priority                   = optional(string, "Regular")
    should_enable_auto_scaling = optional(bool, false)
    min_count                  = optional(number, null)
    max_count                  = optional(number, null)
    zones                      = optional(list(string), null)
    eviction_policy            = optional(string, "Deallocate")
    undrainable_node_behavior  = optional(string, null)
    max_surge                  = optional(string, null)
    max_unavailable            = optional(string, null)
  }))
  description = "Additional AKS node pools configuration. Map key is used as the node pool name. Each entry either owns a subnet through subnet_address_prefixes or shares another entry's subnet through subnet_pool_key. Non-Spot entries can set undrainable_node_behavior (Cordon or Schedule) and one of max_surge or max_unavailable; max_surge defaults to 10% when neither is set. Note: Pod subnets are not used with Azure CNI Overlay mode"
  default = {
    gpu = {
      vm_size                    = "Standard_NV36ads_A10_v5"
      node_count                 = null
      subnet_address_prefixes    = ["10.0.16.0/24"]
      node_taints                = ["nvidia.com/gpu:NoSchedule", "kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
      node_labels                = { accelerator = "nvidia" }
      gpu_driver                 = "Install"
      priority                   = "Spot"
      should_enable_auto_scaling = true
      min_count                  = 0
      max_count                  = 1
      zones                      = []
      eviction_policy            = "Delete"
    }
  }

  validation {
    condition = alltrue([
      for key, pool in var.node_pools :
      pool.subnet_pool_key == null ? true : try(var.node_pools[pool.subnet_pool_key].subnet_pool_key == null, false)
    ])
    error_message = "subnet_pool_key must name another node_pools entry that owns its subnet (an entry that sets subnet_address_prefixes and no subnet_pool_key)."
  }

  validation {
    condition = alltrue([
      for key, pool in var.node_pools :
      (pool.subnet_pool_key == null) == (length(pool.subnet_address_prefixes) > 0)
    ])
    error_message = "Each node_pools entry must set exactly one of subnet_address_prefixes or subnet_pool_key."
  }

  validation {
    condition = alltrue([
      for key, pool in var.node_pools :
      pool.max_surge == null || pool.max_unavailable == null
    ])
    error_message = "A node_pools entry can set max_surge or max_unavailable, not both."
  }

  validation {
    condition = alltrue([
      for key, pool in var.node_pools :
      pool.priority != "Spot" || (pool.max_surge == null && pool.max_unavailable == null && pool.undrainable_node_behavior == null)
    ])
    error_message = "Spot node_pools entries don't support upgrade settings; leave max_surge, max_unavailable, and undrainable_node_behavior unset."
  }

  validation {
    condition = alltrue([
      for key, pool in var.node_pools :
      pool.undrainable_node_behavior == null ? true : contains(["Cordon", "Schedule"], pool.undrainable_node_behavior)
    ])
    error_message = "undrainable_node_behavior must be Cordon or Schedule."
  }
}

/*
 * OSMO Workload Identity Variables
 */

variable "osmo_workload_identity" {
  description = "OSMO workload identity from platform module for federated credential creation"
  type = object({
    id           = string
    principal_id = string
    client_id    = string
    tenant_id    = string
  })
  default = null
}

variable "osmo_config" {
  description = "OSMO configuration for federated identity credentials"
  type = object({
    should_federate_identity = bool
    control_plane_namespace  = string
    operator_namespace       = string
    workflows_namespace      = string
  })
  default = {
    should_federate_identity = false
    control_plane_namespace  = "osmo-control-plane"
    operator_namespace       = "osmo-operator"
    workflows_namespace      = "osmo-workflows"
  }
}

