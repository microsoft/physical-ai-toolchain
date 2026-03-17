/**
 * # DNS Deployment Outputs
 *
 * Outputs from standalone OSMO private DNS zone deployment.
 */

/*
 * OSMO Private DNS Outputs
 */

output "osmo_private_dns_zone" {
  description = "Private DNS zone for OSMO services"
  value = {
    id             = azurerm_private_dns_zone.osmo.id
    name           = azurerm_private_dns_zone.osmo.name
    resource_group = azurerm_private_dns_zone.osmo.resource_group_name
  }
}

output "osmo_fqdn" {
  description = "Fully qualified domain name for OSMO service"
  value       = "${var.osmo_hostname}.${var.osmo_private_dns_zone_name}"
}
