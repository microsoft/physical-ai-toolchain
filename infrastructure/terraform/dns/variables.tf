/**
 * # DNS Deployment Variables
 *
 * Input variables for standalone OSMO private DNS zone deployment.
 */

/*
 * Core Variables - Required
 */

variable "environment" {
  type        = string
  description = "Environment for all resources in this module: dev, test, or prod"
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
  description = "Existing resource group name containing foundational and ML resources (Otherwise 'rg-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

variable "virtual_network_name" {
  type        = string
  description = "Existing virtual network name (Otherwise 'vnet-{resource_prefix}-{environment}-{instance}')"
  default     = null
}

/*
 * OSMO Private DNS Configuration - Required
 */

variable "osmo_loadbalancer_ip" {
  type        = string
  description = "Internal LoadBalancer IP address for the OSMO service"
}

/*
 * OSMO Private DNS Configuration - Optional
 */

variable "osmo_private_dns_zone_name" {
  type        = string
  description = "Private DNS zone name for OSMO services (e.g., osmo.local, osmo.internal)"
  default     = "osmo.local"
}

variable "osmo_hostname" {
  type        = string
  description = "Hostname for the OSMO service within the private DNS zone"
  default     = "dev"
}
