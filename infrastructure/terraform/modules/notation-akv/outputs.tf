/** # Notation AKV Outputs */

output "signing_key_id" {
  description = "Resource ID of the AKV signing key (null when should_deploy = false)."
  value       = try(azurerm_key_vault_key.notation_signing[0].id, null)
}

output "signing_key_versionless_id" {
  description = "Versionless resource ID of the AKV signing key, suitable for Notation key references."
  value       = try(azurerm_key_vault_key.notation_signing[0].versionless_id, null)
}

output "uami_client_id" {
  description = "Client ID of the workload-identity-federated UAMI used for Notation signing."
  value       = try(azurerm_user_assigned_identity.notation_signer[0].client_id, null)
}

output "uami_principal_id" {
  description = "Principal (object) ID of the Notation signer UAMI."
  value       = try(azurerm_user_assigned_identity.notation_signer[0].principal_id, null)
}

output "uami_id" {
  description = "Resource ID of the Notation signer UAMI."
  value       = try(azurerm_user_assigned_identity.notation_signer[0].id, null)
}

output "key_vault_id" {
  description = "Resource ID of the Key Vault holding the signing key (caller-supplied or module-provisioned)."
  value       = local.key_vault_id
}

output "key_vault_uri" {
  description = "URI of the Key Vault holding the signing key."
  value       = local.key_vault_uri
}
