/**
 * # Dataviewer Module Variables
 *
 * Module-specific variables for Container Apps deployment of the dataviewer application.
 */

/*
 * Networking Variables
 */

variable "should_enable_nat_gateway" {
  type        = bool
  description = "Whether to associate NAT Gateway with the Container Apps subnet for outbound connectivity"
  default     = true
}

variable "subnet_address_prefix" {
  type        = string
  description = "Address prefix for the Container Apps infrastructure subnet. Must be /21 or larger"
  default     = "10.0.16.0/21"
}

variable "should_enable_internal" {
  type        = bool
  description = "Whether the Container Apps Environment uses internal load balancing (private access via VNet/VPN). When false, the environment is publicly accessible"
  default     = true
}

/*
 * Container App Configuration
 */

variable "backend_image" {
  type        = string
  description = "Full image reference for the backend container (e.g., acr.azurecr.io/dataviewer-backend:latest). Leave empty to use a placeholder for initial IaC provisioning"
  default     = ""
}

variable "frontend_image" {
  type        = string
  description = "Full image reference for the frontend container (e.g., acr.azurecr.io/dataviewer-frontend:latest). Leave empty to use a placeholder for initial IaC provisioning"
  default     = ""
}

variable "backend_cpu" {
  type        = number
  description = "CPU allocation for the backend container"
  default     = 0.5
}

variable "backend_memory" {
  type        = string
  description = "Memory allocation for the backend container"
  default     = "1Gi"
}

variable "frontend_cpu" {
  type        = number
  description = "CPU allocation for the frontend container"
  default     = 0.25
}

variable "frontend_memory" {
  type        = string
  description = "Memory allocation for the frontend container"
  default     = "0.5Gi"
}

/*
 * Storage Configuration
 */

variable "storage_dataset_container" {
  type        = string
  description = "Name of the Azure Blob Storage container for datasets"
  default     = "datasets"
}

variable "storage_annotation_container" {
  type        = string
  description = "Name of the Azure Blob Storage container for annotations"
  default     = "annotations"
}

/*
 * Authentication Configuration
 */

variable "should_deploy_dataviewer_auth" {
  type        = bool
  description = "Whether to create Entra ID app registration for public mode. Set to false for VNet-only mode"
  default     = false
}

variable "dataviewer_redirect_uris" {
  type        = list(string)
  description = "SPA redirect URIs for MSAL.js authentication (local development)"
  default = [
    "http://localhost:5173/",
    "http://localhost:5174/",
  ]
}
