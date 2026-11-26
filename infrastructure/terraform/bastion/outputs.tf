/**
 * # Bastion Deployment Outputs
 *
 * Outputs from standalone Azure Bastion and private jumpbox deployment.
 */

output "bastion_host" {
  description = "Azure Bastion host resource details"
  value       = module.bastion.bastion_host
}

output "bastion_public_ip" {
  description = "Public IP address details for Azure Bastion"
  value       = module.bastion.bastion_public_ip
}

output "bastion_subnet" {
  description = "AzureBastionSubnet resource details"
  value       = module.bastion.bastion_subnet
}

output "bastion_nsg" {
  description = "Azure Bastion Network Security Group details, or null when disabled"
  value       = module.bastion.bastion_nsg
}

output "jumpbox" {
  description = "Private Linux jumpbox VM details, or null when disabled"
  value       = module.bastion.jumpbox
}

output "connection_info" {
  description = "Operator connection metadata for connect-bastion.sh"
  value       = module.bastion.connection_info
}
