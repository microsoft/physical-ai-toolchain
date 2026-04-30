output "mirror_url" {
  description = "HTTPS endpoint of the Sigstore TUF mirror static website. Null when the module is disabled."
  value       = try(azurerm_storage_account.mirror[0].primary_web_endpoint, null)
}

output "storage_account_id" {
  description = "Resource ID of the mirror Storage Account. Null when the module is disabled."
  value       = try(azurerm_storage_account.mirror[0].id, null)
}

output "storage_account_name" {
  description = "Name of the mirror Storage Account. Null when the module is disabled."
  value       = try(azurerm_storage_account.mirror[0].name, null)
}

output "container_name" {
  description = "Name of the blob container backing the static website ($web). Null when the module is disabled."
  value       = try(azurerm_storage_container.web[0].name, null)
}

output "refresh_schedule_cron" {
  description = "Cron expression recorded for downstream TUF refresh automation."
  value       = var.refresh_schedule_cron
}
