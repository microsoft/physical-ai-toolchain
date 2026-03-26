/**
 * # VPN Deployment Outputs
 *
 * Outputs from standalone VPN gateway deployment.
 */

/*
 * VPN Gateway Outputs
 */

output "vpn_gateway" {
  description = "VPN Gateway resource details"
  value       = module.vpn.vpn_gateway
}

output "vpn_gateway_public_ip" {
  description = "Public IP address of the VPN Gateway"
  value       = module.vpn.vpn_gateway_public_ip
}

output "gateway_subnet" {
  description = "Gateway subnet details"
  value       = module.vpn.gateway_subnet
}

/*
 * P2S Connection Info
 */

output "p2s_connection_info" {
  description = "Point-to-Site VPN connection information"
  value       = module.vpn.p2s_connection_info
}

/*
 * S2S Connection Outputs
 */

output "site_connections" {
  description = "Site-to-Site VPN connection details"
  value       = module.vpn.site_connections
}

output "local_network_gateways" {
  description = "Local network gateway details for each site"
  value       = module.vpn.local_network_gateways
}
