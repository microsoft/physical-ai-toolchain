/**
 * # Sigstore Mirror Variables
 *
 * Inputs controlling the optional air-gapped Sigstore TUF mirror.
 */

variable "should_deploy" {
  description = "When true, provision the Storage Account static website that serves a Sigstore TUF mirror. Defaults to false; consumers using public Rekor/Fulcio do not need this module."
  type        = bool
  default     = false
}

variable "refresh_schedule_cron" {
  description = "Cron expression (UTC) used by downstream automation to refresh TUF metadata into the mirror. The module records the value as a tag; it does not schedule the job itself."
  type        = string
  default     = "0 4 * * *"
}

variable "storage_replication_type" {
  description = "Replication type for the Storage Account hosting the mirror."
  type        = string
  default     = "ZRS"
  validation {
    condition     = contains(["LRS", "ZRS", "GRS", "GZRS", "RAGRS", "RAGZRS"], var.storage_replication_type)
    error_message = "storage_replication_type must be one of LRS, ZRS, GRS, GZRS, RAGRS, RAGZRS."
  }
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
