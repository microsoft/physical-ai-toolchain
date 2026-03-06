/**
 * # Robotics Blueprint Variables
 *
 * Input variables for robotics infrastructure deployment.
 * Variables are organized by functional grouping with required variables first.
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

variable "tags" {
  type        = map(string)
  description = "Tags to apply to all resources"
  default     = {}
}

/*
 * Infrastructure Creation Flags - Optional
 */

variable "should_create_resource_group" {
  type        = bool
  description = "Whether to create the resource group for the robotics infrastructure"
  default     = true
}

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
 * Storage Lifecycle Management
 */

variable "should_enable_raw_bags_lifecycle_policy" {
  type        = bool
  description = "Whether to enable lifecycle policy for raw ROS bags (auto-delete after retention period)"
  default     = true
}

variable "raw_bags_retention_days" {
  type        = number
  description = "Number of days to retain raw ROS bags before automatic deletion. Set to -1 to disable deletion"
  default     = 30

  validation {
    condition     = var.raw_bags_retention_days == -1 || (var.raw_bags_retention_days >= 0 && var.raw_bags_retention_days <= 99999)
    error_message = "raw_bags_retention_days must be -1 (disabled) or between 0 and 99999"
  }
}

variable "should_enable_converted_datasets_lifecycle_policy" {
  type        = bool
  description = "Whether to enable lifecycle policy for converted LeRobot datasets (auto-tier to cool storage)"
  default     = true
}

variable "converted_datasets_cool_tier_days" {
  type        = number
  description = "Number of days before tiering converted datasets to cool storage. Set to -1 to disable tiering"
  default     = 90

  validation {
    condition     = var.converted_datasets_cool_tier_days == -1 || (var.converted_datasets_cool_tier_days >= 0 && var.converted_datasets_cool_tier_days <= 99999)
    error_message = "converted_datasets_cool_tier_days must be -1 (disabled) or between 0 and 99999"
  }
}

variable "should_enable_reports_lifecycle_policy" {
  type        = bool
  description = "Whether to enable lifecycle policy for validation reports (auto-tier to cool then archive)"
  default     = true
}

variable "reports_cool_tier_days" {
  type        = number
  description = "Number of days before tiering validation reports to cool storage"
  default     = 30

  validation {
    condition     = var.reports_cool_tier_days >= 0 && var.reports_cool_tier_days <= 99999
    error_message = "reports_cool_tier_days must be between 0 and 99999"
  }
}

variable "reports_archive_tier_days" {
  type        = number
  description = "Number of days before tiering validation reports to archive storage. Must be greater than reports_cool_tier_days"
  default     = 180

  validation {
    condition     = var.reports_archive_tier_days >= 0 && var.reports_archive_tier_days <= 99999
    error_message = "reports_archive_tier_days must be between 0 and 99999"
  }
}

/*
 * PostgreSQL Configuration
 */

variable "should_deploy_postgresql" {
  type        = bool
  description = "Whether to deploy PostgreSQL Flexible Server component"
  default     = true
}

variable "postgresql_databases" {
  type = map(object({
    collation = string
    charset   = string
  }))
  description = "Map of databases to create with collation and charset"
  default = {
    osmo = {
      collation = "en_US.utf8"
      charset   = "utf8"
    }
  }
}

variable "postgresql_location" {
  type        = string
  description = "Location for PostgreSQL Flexible Server. Defaults to the main location. Set to a different region when PostgreSQL provisioning is restricted in the primary location"
  default     = null
}

variable "postgresql_sku_name" {
  type        = string
  description = "SKU name for PostgreSQL server"
  default     = "GP_Standard_D2s_v3"
}

variable "postgresql_storage_mb" {
  type        = number
  description = "Storage size in megabytes for PostgreSQL"
  default     = 32768
}

variable "postgresql_version" {
  type        = string
  description = "PostgreSQL server version"
  default     = "16"
}

variable "postgresql_zone" {
  type        = string
  description = "Primary availability zone for PostgreSQL. Set to null for Azure auto-selection"
  default     = null
}

variable "postgresql_high_availability" {
  type = object({
    enabled                   = bool
    standby_availability_zone = optional(string)
  })
  description = "PostgreSQL high availability configuration. Set enabled=false to deploy without HA"
  default = {
    enabled                   = false
    standby_availability_zone = null
  }
}

/*
 * Azure Managed Redis Configuration - Optional
 */

variable "should_deploy_redis" {
  type        = bool
  description = "Whether to deploy Azure Managed Redis component"
  default     = true
}

variable "redis_sku_name" {
  type        = string
  description = "SKU name for Azure Managed Redis cache. Format: {Tier}_{Size} (e.g., Balanced_B10, Memory_M20, Compute_X10)"
  default     = "Balanced_B10"
}

variable "redis_clustering_policy" {
  type        = string
  description = "Clustering policy for Redis cache (OSSCluster or EnterpriseCluster). EnterpriseCluster recommended for clients that don't support Redis Cluster MOVED redirects"
  default     = "EnterpriseCluster"

  validation {
    condition     = contains(["OSSCluster", "EnterpriseCluster"], var.redis_clustering_policy)
    error_message = "Clustering policy must be either OSSCluster or EnterpriseCluster."
  }
}

variable "redis_high_availability_enabled" {
  type        = bool
  description = "Enable high availability for Redis. Increases cost but provides zone redundancy"
  default     = false
}

/*
 * OSMO Workload Identity Configuration
 */

variable "osmo_config" {
  description = "OSMO configuration including workload identity settings"
  type = object({
    should_enable_identity   = bool
    should_federate_identity = bool
    control_plane_namespace  = string
    operator_namespace       = string
    workflows_namespace      = string
  })
  default = {
    should_enable_identity   = true
    should_federate_identity = true
    control_plane_namespace  = "osmo-control-plane"
    operator_namespace       = "osmo-operator"
    workflows_namespace      = "osmo-workflows"
  }
}

/*
 * Resource Name Overrides - Optional
 */

variable "resource_group_name" {
  type        = string
  description = "Existing resource group name containing foundational and ML resources (Otherwise 'rg-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

/*
 * Networking Configuration - Optional
 */

variable "should_enable_nat_gateway" {
  type        = bool
  description = "Whether to deploy NAT Gateway for explicit outbound connectivity. When true, subnets use NAT Gateway; when false, subnets use Azure default outbound access"
  default     = true
}

variable "virtual_network_config" {
  type = object({
    address_space                  = string
    subnet_address_prefix          = string
    subnet_address_prefix_pe       = optional(string, "10.0.2.0/24")
    subnet_address_prefix_resolver = optional(string, "10.0.9.0/28")
  })
  description = "Configuration for the virtual network including address space and subnet prefixes. PE subnet prefix is required when private endpoints are enabled. Resolver subnet enables DNS resolution for VPN clients and on-premises networks"
  default = {
    address_space                  = "10.0.0.0/16"
    subnet_address_prefix          = "10.0.1.0/24"
    subnet_address_prefix_pe       = "10.0.2.0/24"
    subnet_address_prefix_resolver = "10.0.9.0/28"
  }
  validation {
    condition     = can(cidrhost(var.virtual_network_config.address_space, 0)) && can(cidrhost(var.virtual_network_config.subnet_address_prefix, 0))
    error_message = "Both address_space and subnet_address_prefix must be valid CIDR blocks."
  }
}

variable "subnet_address_prefixes_aks" {
  type        = list(string)
  description = "Address prefixes for the AKS subnet"
  default     = ["10.0.5.0/24"]
}

variable "subnet_address_prefixes_aks_pod" {
  type        = list(string)
  description = "Address prefixes for the AKS pod subnet"
  default     = ["10.0.6.0/24"]
}

/*
 * AKS System Node Pool Configuration - Optional
 */

variable "system_node_pool_vm_size" {
  type        = string
  description = "VM size for the AKS system node pool"
  default     = "Standard_D8ds_v5"
}

variable "system_node_pool_node_count" {
  type        = number
  description = "Number of nodes for the AKS system node pool"
  default     = 1
}

variable "system_node_pool_enable_auto_scaling" {
  type        = bool
  description = "Enable auto-scaling for the AKS system node pool"
  default     = false
}

variable "system_node_pool_min_count" {
  type        = number
  description = "Minimum node count for AKS system node pool when auto-scaling is enabled (0-1000)"
  default     = null
}

variable "system_node_pool_max_count" {
  type        = number
  description = "Maximum node count for AKS system node pool when auto-scaling is enabled (0-1000)"
  default     = null
}

variable "system_node_pool_zones" {
  type        = list(string)
  description = "Availability zones for AKS system node pool. Set to null or empty for regional deployment (no zone constraint)"
  default     = null
}

/*
 * GPU Node Pool Configuration - Optional
 */

variable "node_pools" {
  type = map(object({
    node_count              = optional(number, null)
    vm_size                 = string
    subnet_address_prefixes = list(string)
    node_taints             = optional(list(string), [])
    node_labels             = optional(map(string), {})
    enable_auto_scaling     = optional(bool, false)
    min_count               = optional(number, null)
    max_count               = optional(number, null)
    priority                = optional(string, "Regular")
    zones                   = optional(list(string), null)
    eviction_policy         = optional(string, "Deallocate")
    gpu_driver              = optional(string, null)
  }))
  description = "Additional node pools for the AKS cluster. Map key is used as the node pool name. Note: Pod subnets are not used with Azure CNI Overlay mode"
  default = {
    gpu = {
      vm_size                 = "Standard_NV36ads_A10_v5"
      subnet_address_prefixes = ["10.0.7.0/24"]
      node_taints             = ["nvidia.com/gpu:NoSchedule", "kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
      gpu_driver              = "Install"
      priority                = "Spot"
      enable_auto_scaling     = true
      min_count               = 1
      max_count               = 1
      zones                   = []
      eviction_policy         = "Delete"
    }
  }
}

/*
 * Private Endpoints Configuration - Optional
 */

variable "should_enable_private_endpoint" {
  type        = bool
  description = "Whether to enable private endpoints across resources for secure connectivity"
  default     = true
}

variable "should_enable_private_aks_cluster" {
  type        = bool
  description = "Whether the AKS cluster API endpoint is private. When true, requires VPN for kubectl access. Can be set independently from should_enable_private_endpoint to allow private Azure services with a public AKS control plane."
  default     = true
}

/*
 *  Public Network Access Configuration - Optional
 */

variable "should_enable_public_network_access" {
  type        = bool
  description = "Whether to enable public network access to the Azure ML workspace"
  default     = true
}

variable "should_enable_microsoft_defender" {
  type        = bool
  description = "Whether to enable Microsoft Defender for Containers on the AKS cluster"
  default     = false
}

/*
 * Observability Feature Flags - Optional
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
 * AzureML Compute Configuration - Optional
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
 * DNS Zone Feature Flags - Optional
 */

variable "should_include_aks_dns_zone" {
  type        = bool
  description = "Whether to include the AKS private DNS zone in core DNS zones"
  default     = true
}
