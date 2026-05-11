/**
 * # Module Variables
 *
 * Conversion-pipeline-specific knobs: Event Grid filters, Fabric capacity
 * sizing, and downstream subscriber wiring. Storage shape, lifecycle, and
 * private-endpoint inputs are intentionally absent: durable storage is owned
 * by the platform module's data-lake account.
 */

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
  description = "Whether to provision a Fabric workspace bound to the Fabric capacity. The workspace's capacity_id is resolved at apply time from a deferred data \"fabric_capacity\" lookup keyed on the capacity display name"
  default     = true
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
  description = "Object ID of the Fabric workspace service principal. When provided, the SP is granted Storage Blob Data Reader on the datasets container plus an ADLS Gen2 ACL granting rwx on the converted/ folder"
  default     = null
}
