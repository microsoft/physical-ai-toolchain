/**
 * # Module Variables
 *
 * Conversion-pipeline-specific knobs: storage shape, lifecycle days, Event Grid
 * filters, Fabric capacity sizing, and downstream subscriber wiring.
 */

/*
 * Storage Account Shape
 */

variable "storage_replication_type" {
  type        = string
  description = "Storage account replication type. LRS for dev, ZRS for staging, GRS/RA-GRS for prod"
  default     = "ZRS"

  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "RA-GRS", "GZRS", "RA-GZRS"], var.storage_replication_type)
    error_message = "storage_replication_type must be one of LRS, ZRS, GRS, RA-GRS, GZRS, RA-GZRS."
  }
}

variable "should_enable_shared_key" {
  type        = bool
  description = "Whether to allow shared-key access on the storage account. Defaults to false (Entra-only). Enable only when an integration explicitly requires it"
  default     = false
}

variable "should_enable_public_network_access" {
  type        = bool
  description = "Whether to allow public network access to the storage account. Defaults to false; should be true only for dev"
  default     = false
}

variable "allowed_ip_rules" {
  type        = list(string)
  description = "Optional list of public IP addresses or CIDR ranges allowed when public network access is enabled"
  default     = []
}

variable "blob_soft_delete_days" {
  type        = number
  description = "Soft-delete retention in days for blobs"
  default     = 7

  validation {
    condition     = var.blob_soft_delete_days >= 7 && var.blob_soft_delete_days <= 365
    error_message = "blob_soft_delete_days must be between 7 and 365."
  }
}

variable "container_soft_delete_days" {
  type        = number
  description = "Soft-delete retention in days for containers"
  default     = 7

  validation {
    condition     = var.container_soft_delete_days >= 7 && var.container_soft_delete_days <= 365
    error_message = "container_soft_delete_days must be between 7 and 365."
  }
}

/*
 * Lifecycle Policies
 */

variable "should_enable_raw_lifecycle" {
  type        = bool
  description = "Whether to enable the raw blob deletion lifecycle rule"
  default     = true
}

variable "raw_retention_days" {
  type        = number
  description = "Number of days to retain raw ROS bags before automatic deletion. Set to -1 to disable"
  default     = 30

  validation {
    condition     = var.raw_retention_days == -1 || (var.raw_retention_days >= 0 && var.raw_retention_days <= 99999)
    error_message = "raw_retention_days must be -1 (disabled) or between 0 and 99999."
  }
}

variable "should_enable_converted_lifecycle" {
  type        = bool
  description = "Whether to enable the converted blob tiering lifecycle rule"
  default     = true
}

variable "converted_cool_days" {
  type        = number
  description = "Days before tiering converted blobs to cool storage. Set to -1 to disable"
  default     = 30

  validation {
    condition     = var.converted_cool_days == -1 || (var.converted_cool_days >= 0 && var.converted_cool_days <= 99999)
    error_message = "converted_cool_days must be -1 (disabled) or between 0 and 99999."
  }
}

variable "converted_archive_days" {
  type        = number
  description = "Days before tiering converted blobs to archive. Set to -1 to disable"
  default     = 90

  validation {
    condition     = var.converted_archive_days == -1 || (var.converted_archive_days >= 0 && var.converted_archive_days <= 99999)
    error_message = "converted_archive_days must be -1 (disabled) or between 0 and 99999."
  }
}

/*
 * Networking
 */

variable "should_enable_private_endpoint" {
  type        = bool
  description = "Whether to provision private endpoints for the storage account's blob and dfs subresources"
  default     = true
}

/*
 * Event Grid
 */

variable "should_enable_event_grid_dead_letter" {
  type        = bool
  description = "Whether to enable an Event Grid dead-letter destination backed by an in-account container"
  default     = true
}

variable "raw_blob_suffix_filters" {
  type        = list(string)
  description = "Suffix filters used by the Event Grid subscription's advanced_filter.string_ends_with on the raw container"
  default     = [".bag", ".bag.zst", ".mcap"]

  validation {
    condition     = length(var.raw_blob_suffix_filters) > 0
    error_message = "raw_blob_suffix_filters must contain at least one suffix."
  }
}

variable "conversion_subscriber_url" {
  type        = string
  description = "Optional webhook URL for the downstream conversion subscriber. When null, the subscription is created without a webhook destination (DLQ-only) until the conversion compute lands"
  default     = null
}

/*
 * Microsoft Fabric
 */

variable "should_create_fabric_capacity" {
  type        = bool
  description = "Whether to provision a new Fabric capacity"
  default     = true
}

variable "should_create_fabric_workspace" {
  type        = bool
  description = "Whether to provision a Fabric workspace bound to fabric_capacity_uuid. The workspace is only created when fabric_capacity_uuid is also non-null. See README two-pass deployment guidance"
  default     = true
}

variable "fabric_capacity_uuid" {
  type        = string
  description = "Fabric capacity GUID (UUID format) used as fabric_workspace.capacity_id. Must be supplied after the azurerm_fabric_capacity is created (the GUID is not exposed by the azurerm provider). When null, the workspace is not created"
  default     = null

  validation {
    condition     = var.fabric_capacity_uuid == null || can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.fabric_capacity_uuid))
    error_message = "fabric_capacity_uuid must be null or a valid UUID."
  }
}

variable "fabric_capacity_sku" {
  type        = string
  description = "SKU for the Fabric capacity (F2 through F2048). Only used when should_create_fabric_capacity is true"
  default     = "F2"

  validation {
    condition = contains([
      "F2", "F4", "F8", "F16", "F32", "F64", "F128", "F256", "F512", "F1024", "F2048"
    ], var.fabric_capacity_sku)
    error_message = "fabric_capacity_sku must be one of F2, F4, F8, F16, F32, F64, F128, F256, F512, F1024, F2048."
  }
}

variable "fabric_admin_members" {
  type        = list(string)
  description = "Entra UPNs or object IDs that should be granted Fabric capacity administration"
  default     = []
}

variable "fabric_workspace_sp_object_id" {
  type        = string
  description = "Object ID of the Fabric workspace service principal. When provided, RBAC is granted on the raw (read) and converted (contributor) containers"
  default     = null
}

/*
 * Diagnostics
 */

variable "should_enable_diagnostic_settings" {
  type        = bool
  description = "Whether to route storage account and blob diagnostics to the supplied Log Analytics workspace"
  default     = true
}
