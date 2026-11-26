/**
 * # Dependency Variables
 *
 * External resource references passed from platform and workload modules.
 */

variable "virtual_network" {
  type = object({
    id   = string
    name = string
  })
  description = "Virtual network object from platform module"
}

variable "jumpbox_subnet" {
  type = object({
    id   = string
    name = string
  })
  description = "Target subnet object for the private jumpbox NIC (required when should_deploy_jumpbox is true)"
  default     = null

  validation {
    condition     = !var.should_deploy_jumpbox || var.jumpbox_subnet != null
    error_message = "jumpbox_subnet must be provided when should_deploy_jumpbox is true."
  }
}

variable "aks_cluster_id" {
  type        = string
  description = "Optional AKS cluster resource ID for granting the jumpbox identity Azure Kubernetes Service Cluster User Role"
  default     = null
}

variable "container_registry_id" {
  type        = string
  description = "Optional Azure Container Registry resource ID for granting the jumpbox identity AcrPull"
  default     = null
}

variable "key_vault_id" {
  type        = string
  description = "Optional Azure Key Vault resource ID for granting the jumpbox identity Key Vault Secrets User"
  default     = null
}

variable "storage_account_id" {
  type        = string
  description = "Optional Azure Storage Account resource ID for granting the jumpbox identity Storage Blob Data Reader"
  default     = null
}
