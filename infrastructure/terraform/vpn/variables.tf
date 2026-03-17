/**
 * # VPN Deployment Variables
 *
 * Input variables for standalone VPN gateway deployment.
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

/*
 * VPN Gateway Configuration - Required
 */

variable "gateway_subnet_address_prefix" {
  type        = string
  description = "Address prefix for the GatewaySubnet (minimum /27)"
  default     = "10.0.3.0/27"

  validation {
    condition     = can(cidrhost(var.gateway_subnet_address_prefix, 0))
    error_message = "gateway_subnet_address_prefix must be a valid CIDR block."
  }
}

/*
 * VPN Gateway Configuration - Optional
 */

variable "vpn_gateway_config" {
  type = object({
    sku                 = optional(string, "VpnGw1")
    generation          = optional(string, "Generation1")
    client_address_pool = optional(list(string), ["192.168.200.0/24"])
  })
  description = "VPN Gateway configuration including SKU, generation, and P2S client address pool"
  default     = {}

  validation {
    condition     = contains(["VpnGw1", "VpnGw2", "VpnGw3", "VpnGw1AZ", "VpnGw2AZ", "VpnGw3AZ"], var.vpn_gateway_config.sku)
    error_message = "vpn_gateway_config.sku must be a valid VPN Gateway SKU."
  }
}

/*
 * P2S Certificate Authentication - Optional
 */

variable "root_certificate_name" {
  type        = string
  description = "Name for the root certificate used in P2S authentication"
  default     = "RoboticsVPNRootCert"
}

variable "root_certificate_public_data" {
  type        = string
  description = "Base64-encoded public certificate data for P2S authentication (without BEGIN/END markers)"
  default     = null
}

/*
 * P2S Azure AD Authentication - Optional
 */

variable "aad_auth_config" {
  type = object({
    enabled     = bool
    tenant_id   = optional(string, null)
    audience_id = optional(string, "c632b3df-fb67-4d84-bdcf-b95ad541b5c8")
  })
  description = "Azure AD authentication configuration for P2S VPN. tenant_id defaults to current Azure client tenant if not specified. Uses Microsoft-registered Azure VPN application by default. Requires OpenVPN protocol"
  default = {
    enabled = true
  }
}

/*
 * Site-to-Site VPN Configuration - Optional
 */

variable "vpn_site_connections" {
  type = list(object({
    name                 = string
    address_spaces       = list(string)
    shared_key_reference = string
    gateway_ip_address   = optional(string)
    gateway_fqdn         = optional(string)
    bgp_asn              = optional(number)
    bgp_peering_address  = optional(string)
    ike_protocol         = optional(string, "IKEv2")
  }))
  description = "Site-to-site VPN site definitions for connecting on-premises networks"
  default     = []
}

variable "vpn_site_shared_keys" {
  type        = map(string)
  description = "Pre-shared keys for S2S VPN connections indexed by shared_key_reference"
  sensitive   = true
  default     = {}
}

variable "vpn_site_default_ipsec_policy" {
  type = object({
    dh_group            = string
    ike_encryption      = string
    ike_integrity       = string
    ipsec_encryption    = string
    ipsec_integrity     = string
    pfs_group           = string
    sa_datasize_kb      = optional(number)
    sa_lifetime_seconds = optional(number)
  })
  description = "Default IPsec policy for all S2S connections"
  default     = null
}
