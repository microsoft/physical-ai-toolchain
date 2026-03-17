/**
 * # Platform Module Variables
 *
 * Module-specific variables for platform infrastructure including networking,
 * observability, security, and Azure ML workspace configuration.
 */

/*
 * Current User Configuration
 */

variable "current_user_oid" {
  type        = string
  description = "Object ID of the current user for role assignments. Obtained via Microsoft Graph to avoid constant updates from azurerm_client_config"
  default     = null
}

/*
 * Networking Variables
 */

variable "should_enable_nat_gateway" {
  type        = bool
  description = "Whether to deploy NAT Gateway for explicit outbound connectivity. When true, subnets use NAT Gateway; when false, subnets use Azure default outbound access"
  default     = true
}

variable "virtual_network_config" {
  type = object({
    address_space                  = string
    subnet_address_prefix_main     = string
    subnet_address_prefix_pe       = optional(string)
    subnet_address_prefix_resolver = optional(string)
  })
  description = "Virtual network address configuration including address space and subnet prefixes. PE and resolver subnet prefixes are only used when should_enable_private_endpoint is true"
  default = {
    address_space                  = "10.0.0.0/16"
    subnet_address_prefix_main     = "10.0.1.0/24"
    subnet_address_prefix_pe       = "10.0.2.0/24"
    subnet_address_prefix_resolver = "10.0.9.0/28"
  }
}

/*
 * Private Endpoint Variables
 */

variable "should_enable_private_endpoint" {
  type        = bool
  description = "Whether to enable private endpoints for all services"
  default     = true
}

variable "should_enable_public_network_access" {
  type        = bool
  description = "Whether to allow public network access (set to true for dev/test)"
  default     = false
}

/*
 * Security Variables
 */

variable "should_add_current_user_key_vault_admin" {
  type        = bool
  description = "Whether to add the current user as Key Vault Secrets Officer"
  default     = true
}

variable "should_add_current_user_storage_blob" {
  type        = bool
  description = "Whether to add the current user as Storage Blob Data Contributor"
  default     = true
}

variable "should_enable_purge_protection" {
  type        = bool
  description = "Whether to enable purge protection on Key Vault. Set to false for dev/test to allow easy cleanup. WARNING: Once enabled, purge protection cannot be disabled"
  default     = false
}

/*
 * OSMO Variables - PostgreSQL
 */

variable "should_deploy_postgresql" {
  type        = bool
  description = "Whether to deploy PostgreSQL for OSMO backend"
  default     = false
}

variable "postgresql_config" {
  type = object({
    location                  = string
    sku_name                  = string
    storage_mb                = number
    version                   = string
    databases                 = map(object({ collation = string, charset = string }))
    zone                      = optional(string)
    high_availability_enabled = optional(bool, false)
    standby_availability_zone = optional(string)
  })
  description = "PostgreSQL configuration for OSMO including location, SKU, storage, zone, HA settings, and database definitions"
  default = {
    location                  = "westus3"
    sku_name                  = "GP_Standard_D2s_v3"
    storage_mb                = 32768
    version                   = "16"
    databases                 = { osmo = { collation = "en_US.utf8", charset = "utf8" } }
    zone                      = null
    high_availability_enabled = false
    standby_availability_zone = null
  }
}

/*
 * OSMO Variables - Redis
 */

variable "should_deploy_redis" {
  type        = bool
  description = "Whether to deploy Azure Managed Redis for OSMO"
  default     = false
}

variable "redis_config" {
  type = object({
    sku_name                  = string
    clustering_policy         = string
    high_availability_enabled = optional(bool, false)
  })
  description = "Redis configuration for OSMO including SKU, clustering policy, and HA settings. EnterpriseCluster recommended for clients that don't support Redis Cluster MOVED redirects"
  default = {
    sku_name                  = "Balanced_B10"
    clustering_policy         = "EnterpriseCluster"
    high_availability_enabled = false
  }
}

/*
 * OSMO Variables - Workload Identity
 */

variable "should_enable_osmo_identity" {
  type        = bool
  description = "Whether to create a managed identity for OSMO workload identity authentication"
  default     = true
}

/*
 * Storage Variables
 */

variable "should_enable_storage_shared_access_key" {
  type        = bool
  description = "Whether to enable Shared Key (SAS token) authorization for the storage account. When false, all requests must use Azure AD authentication"
  default     = false
}

/*
 * Observability Feature Flags
 */

variable "should_deploy_grafana" {
  type        = bool
  description = "Whether to deploy Azure Managed Grafana dashboard"
  default     = true
}

variable "should_deploy_monitor_workspace" {
  type        = bool
  description = "Whether to deploy Azure Monitor Workspace for Prometheus metrics"
  default     = true
}

variable "should_deploy_ampls" {
  type        = bool
  description = "Whether to deploy Azure Monitor Private Link Scope and its private endpoint"
  default     = true
}

variable "should_deploy_dce" {
  type        = bool
  description = "Whether to deploy Data Collection Endpoint for observability"
  default     = true
}

/*
 * AzureML Compute Configuration
 */

variable "should_deploy_aml_compute" {
  type        = bool
  description = "Whether to deploy an AzureML managed compute cluster for GPU workloads"
  default     = false
}

variable "aml_compute_config" {
  type = object({
    vm_size               = string
    vm_priority           = string
    min_node_count        = number
    max_node_count        = number
    scale_down_after_idle = optional(string, "PT5M")
    cluster_name          = optional(string, "gpu-cluster")
    subnet_id             = optional(string)
  })
  description = "AzureML managed compute cluster configuration including VM size, priority, scaling, and optional subnet placement"
  default = {
    vm_size               = "Standard_NC4as_T4_v3"
    vm_priority           = "LowPriority"
    min_node_count        = 0
    max_node_count        = 1
    scale_down_after_idle = "PT5M"
    cluster_name          = "gpu-cluster"
    subnet_id             = null
  }
}

/*
 * DNS Zone Feature Flags
 */

variable "should_include_aks_dns_zone" {
  type        = bool
  description = "Whether to include the AKS private DNS zone in core DNS zones"
  default     = true
}
