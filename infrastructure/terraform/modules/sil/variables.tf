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
    system_node_pool_vm_size             = string
    system_node_pool_node_count          = number
    system_node_pool_enable_auto_scaling = bool
    system_node_pool_min_count           = optional(number)
    system_node_pool_max_count           = optional(number)
    is_private_cluster                   = bool
    system_node_pool_zones               = optional(list(string))
    should_enable_microsoft_defender     = optional(bool, false)
  })
  description = "AKS cluster configuration for the system node pool"
  default = {
    system_node_pool_vm_size             = "Standard_D8ds_v5"
    system_node_pool_node_count          = 2
    system_node_pool_enable_auto_scaling = false
    system_node_pool_min_count           = null
    system_node_pool_max_count           = null
    is_private_cluster                   = true
    system_node_pool_zones               = null
  }
}

variable "node_pools" {
  type = map(object({
    vm_size                 = string
    node_count              = optional(number, null)
    subnet_address_prefixes = list(string)
    node_taints             = optional(list(string), [])
    node_labels             = optional(map(string), {})
    gpu_driver              = optional(string)
    priority                = optional(string, "Regular")
    enable_auto_scaling     = optional(bool, false)
    min_count               = optional(number, null)
    max_count               = optional(number, null)
    zones                   = optional(list(string), null)
    eviction_policy         = optional(string, "Deallocate")
  }))
  description = "Additional AKS node pools configuration. Map key is used as the node pool name. Note: Pod subnets are not used with Azure CNI Overlay mode"
  default = {
    gpu = {
      vm_size                 = "Standard_NV36ads_A10_v5"
      node_count              = null
      subnet_address_prefixes = ["10.0.16.0/24"]
      node_taints             = ["nvidia.com/gpu:NoSchedule", "kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
      gpu_driver              = "Install"
      priority                = "Spot"
      enable_auto_scaling     = true
      min_count               = 0
      max_count               = 1
      zones                   = []
      eviction_policy         = "Delete"
    }
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

/*
 * Key Vault Variables
 */

