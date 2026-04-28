/**
 * # Dependency Variables
 *
 * Resources provided by the platform module as typed object dependencies.
 */

variable "data_lake_storage_account" {
  type = object({
    id   = string
    name = string
  })
  description = "Platform-owned ADLS Gen2 data-lake account (stdl...) used as the durable raw -> converted store"
}

variable "datasets_container" {
  type = object({
    id   = string
    name = string
  })
  description = "Datasets container on the platform-owned data-lake account. Used to scope Fabric SP ACL grants to raw/ and converted/ folders"
}
