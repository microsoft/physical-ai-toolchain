/**
 * # Bastion Deployment Variables
 *
 * Input variables for standalone Azure Bastion and private jumpbox deployment.
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

variable "resource_group_name" {
  type        = string
  description = "Existing resource group name containing foundational resources (Otherwise 'rg-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

variable "virtual_network_name" {
  type        = string
  description = "Existing virtual network name (Otherwise 'vnet-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

variable "jumpbox_subnet_name" {
  type        = string
  description = "Existing subnet name for the private jumpbox NIC (Otherwise 'snet-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

/*
 * Bastion Networking Configuration
 */

variable "bastion_subnet_address_prefix" {
  type        = string
  description = "Address prefix for AzureBastionSubnet (minimum /26, non-overlapping with platform subnets)"
  default     = "10.0.10.0/26"

  validation {
    condition     = can(cidrhost(var.bastion_subnet_address_prefix, 0))
    error_message = "bastion_subnet_address_prefix must be a valid CIDR block."
  }

  validation {
    condition     = can(cidrhost(var.bastion_subnet_address_prefix, 0)) ? tonumber(split("/", var.bastion_subnet_address_prefix)[1]) <= 26 : true
    error_message = "bastion_subnet_address_prefix must have a prefix length of /26 or larger."
  }
}

variable "should_create_bastion_nsg" {
  type        = bool
  description = "Whether to create and associate the Microsoft-compliant Network Security Group for AzureBastionSubnet"
  default     = true
}

variable "bastion_allowed_source_address_prefix" {
  type        = string
  description = "Source address prefix or service tag allowed to connect to Azure Bastion over HTTPS (port 443)"
  default     = "Internet"
}

/*
 * Azure Bastion Configuration
 */

variable "bastion_config" {
  type = object({
    sku                    = optional(string, "Standard")
    scale_units            = optional(number, 2)
    tunneling_enabled      = optional(bool, true)
    copy_paste_enabled     = optional(bool, true)
    file_copy_enabled      = optional(bool, false)
    ip_connect_enabled     = optional(bool, false)
    shareable_link_enabled = optional(bool, false)
    zones                  = optional(list(string), ["1", "2", "3"])
  })
  description = "Azure Bastion host configuration including SKU, scale units, native tunneling, and availability zones"
  default     = {}

  validation {
    condition     = contains(["Standard", "Premium"], var.bastion_config.sku)
    error_message = "bastion_config.sku must be Standard or Premium to support native tunneling."
  }

  validation {
    condition     = var.bastion_config.scale_units >= 2 && var.bastion_config.scale_units <= 50
    error_message = "bastion_config.scale_units must be between 2 and 50."
  }
}

/*
 * Private Jumpbox Configuration
 */

variable "should_deploy_jumpbox" {
  type        = bool
  description = "Whether to deploy the private Entra-authenticated Linux jumpbox VM in the platform subnet"
  default     = true
}

variable "jumpbox_config" {
  type = object({
    vm_size                      = optional(string, "Standard_B2s")
    admin_username               = optional(string, "azureuser")
    admin_ssh_public_key         = optional(string, null)
    os_disk_size_gb              = optional(number, 64)
    os_disk_storage_account_type = optional(string, "Premium_LRS")
    zone                         = optional(string, null)
    should_enable_cloud_init     = optional(bool, true)
    custom_cloud_init            = optional(string, null)
  })
  description = "Private Linux jumpbox VM configuration including size, SSH key, OS disk settings, and optional cloud-init"
  default     = {}

  validation {
    condition     = var.jumpbox_config.admin_ssh_public_key == null ? true : can(regex("^(ssh-(rsa|ed25519)|ecdsa-sha2-nistp(256|384|521))\\s+[A-Za-z0-9+/=]+", trimspace(var.jumpbox_config.admin_ssh_public_key)))
    error_message = "jumpbox_config.admin_ssh_public_key must be a valid SSH public key (ssh-rsa, ssh-ed25519, or ecdsa-sha2-nistp*)."
  }

  validation {
    condition     = !var.should_deploy_jumpbox || (var.jumpbox_config.admin_ssh_public_key != null && trimspace(var.jumpbox_config.admin_ssh_public_key) != "")
    error_message = "jumpbox_config.admin_ssh_public_key is required when should_deploy_jumpbox is true."
  }

  validation {
    condition     = var.jumpbox_config.os_disk_size_gb >= 30
    error_message = "jumpbox_config.os_disk_size_gb must be at least 30 GB."
  }

  validation {
    condition     = contains(["Standard_LRS", "StandardSSD_LRS", "Premium_LRS", "StandardSSD_ZRS", "Premium_ZRS"], var.jumpbox_config.os_disk_storage_account_type)
    error_message = "jumpbox_config.os_disk_storage_account_type must be a valid Azure managed disk SKU."
  }
}

/*
 * Optional RBAC Targets & Entra ID Login Configuration
 */

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

variable "should_assign_current_user_admin_login" {
  type        = bool
  description = "Whether to grant the deploying principal Virtual Machine Administrator Login on the jumpbox VM"
  default     = false
}

variable "current_user_oid" {
  type        = string
  description = "Optional Entra ID object ID for current user VM admin login; defaults to the authenticated Terraform client object ID"
  default     = null
}

variable "jumpbox_admin_principal_ids" {
  type        = list(string)
  description = "Entra ID object IDs granted Virtual Machine Administrator Login on the jumpbox VM"
  default     = []
}

variable "jumpbox_user_principal_ids" {
  type        = list(string)
  description = "Entra ID object IDs granted Virtual Machine User Login on the jumpbox VM"
  default     = []
}

// === Tags ===

variable "tags" {
  description = "Tags to apply to all resources created by this deployment"
  type        = map(string)
  default     = {}
}
