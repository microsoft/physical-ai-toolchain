/**
 * # Platform Module Outputs
 *
 * Typed object outputs for consumption by the SiL module.
 * All outputs are structured as objects with selected fields for type safety.
 */

/*
 * Networking Outputs
 */

output "virtual_network" {
  description = "Virtual network for SiL AKS cluster"
  value = {
    id   = azurerm_virtual_network.main.id
    name = azurerm_virtual_network.main.name
  }
}

output "subnets" {
  description = "Subnets for SiL resources. Private endpoints subnet is null when private endpoints are disabled"
  value = {
    main = {
      id   = azurerm_subnet.main.id
      name = azurerm_subnet.main.name
    }
    private_endpoints = try({
      id   = azurerm_subnet.private_endpoints[0].id
      name = azurerm_subnet.private_endpoints[0].name
    }, null)
  }
}

output "network_security_group" {
  description = "NSG for SiL subnets"
  value = {
    id = azurerm_network_security_group.main.id
  }
}

output "nat_gateway" {
  description = "NAT Gateway for outbound connectivity. Null when NAT Gateway is disabled"
  value = try({
    id = azurerm_nat_gateway.main[0].id
  }, null)
}

/*
 * DNS Private Resolver Outputs
 */

output "private_dns_resolver" {
  description = "Private DNS Resolver for resolving private DNS zones. Null when private endpoints are disabled or resolver subnet not configured"
  value = try({
    id   = azurerm_private_dns_resolver.main[0].id
    name = azurerm_private_dns_resolver.main[0].name
  }, null)
}

output "dns_server_ip" {
  description = "The IP address to use as DNS server for VPN clients or on-premises DNS forwarding. Null when resolver not configured"
  value       = try(azurerm_private_dns_resolver_inbound_endpoint.main[0].ip_configurations[0].private_ip_address, null)
}

/*
 * Observability Outputs
 */

output "log_analytics_workspace" {
  description = "Log Analytics workspace for AKS monitoring"
  value = {
    id           = azurerm_log_analytics_workspace.main.id
    workspace_id = azurerm_log_analytics_workspace.main.workspace_id
  }
}

output "monitor_workspace" {
  description = "Azure Monitor workspace for Prometheus metrics. Null when monitor workspace is disabled"
  value = try({
    id = azurerm_monitor_workspace.main[0].id
  }, null)
}

output "data_collection_endpoint" {
  description = "Data Collection Endpoint for observability. Null when DCE is disabled"
  value = try({
    id = azurerm_monitor_data_collection_endpoint.main[0].id
  }, null)
}

output "application_insights" {
  description = "Application Insights for telemetry"
  value = {
    id                  = azurerm_application_insights.main.id
    connection_string   = azurerm_application_insights.main.connection_string
    instrumentation_key = azurerm_application_insights.main.instrumentation_key
  }
  sensitive = true
}

output "grafana" {
  description = "Azure Managed Grafana dashboard. Null when Grafana is disabled"
  value = try({
    id       = azurerm_dashboard_grafana.main[0].id
    endpoint = azurerm_dashboard_grafana.main[0].endpoint
  }, null)
}

/*
 * Security Outputs
 */

output "key_vault" {
  description = "Key Vault for secrets management"
  value = {
    id        = azurerm_key_vault.main.id
    name      = azurerm_key_vault.main.name
    vault_uri = azurerm_key_vault.main.vault_uri
  }
}

/*
 * ACR Output
 */

output "container_registry" {
  description = "Container registry for SiL workloads"
  value = {
    id           = azurerm_container_registry.main.id
    name         = azurerm_container_registry.main.name
    login_server = azurerm_container_registry.main.login_server
  }
}

/*
 * Storage Output
 */

output "storage_account" {
  description = "Storage account for ML workspace"
  value = {
    id   = azurerm_storage_account.main.id
    name = azurerm_storage_account.main.name
  }
}

output "storage_account_access" {
  description = "Storage account access credentials. Only populated when shared_access_key_enabled is true"
  value = {
    primary_blob_endpoint = azurerm_storage_account.main.primary_blob_endpoint
    primary_access_key    = azurerm_storage_account.main.primary_access_key
  }
  sensitive = true
}

/*
 * AzureML Outputs
 */

output "azureml_workspace" {
  description = "ML workspace for AKS extension."
  value = {
    id           = azapi_resource.ml_workspace.id
    name         = azapi_resource.ml_workspace.name
    workspace_id = azapi_resource.ml_workspace.output.properties.workspaceId
  }
}

output "ml_workload_identity" {
  description = "ML workload identity for FICs"
  value = {
    id           = azurerm_user_assigned_identity.ml.id
    principal_id = azurerm_user_assigned_identity.ml.principal_id
    client_id    = azurerm_user_assigned_identity.ml.client_id
    tenant_id    = azurerm_user_assigned_identity.ml.tenant_id
  }
}

output "aml_compute_cluster" {
  description = "AzureML managed compute cluster. Null when compute deployment is disabled"
  value = try({
    id   = azurerm_machine_learning_compute_cluster.gpu[0].id
    name = azurerm_machine_learning_compute_cluster.gpu[0].name
  }, null)
}

/*
 * DNS Zones Output
 */

output "private_dns_zones" {
  description = "Private DNS zones for private endpoints"
  value = try({
    for key, zone in azurerm_private_dns_zone.core : key => {
      id   = zone.id
      name = zone.name
    }
  }, {})
}

/*
 * OSMO Outputs (Optional)
 */

output "postgresql" {
  description = "PostgreSQL Flexible Server for OSMO (if deployed)"
  value = try({
    id             = azurerm_postgresql_flexible_server.main[0].id
    fqdn           = azurerm_postgresql_flexible_server.main[0].fqdn
    name           = azurerm_postgresql_flexible_server.main[0].name
    admin_username = azurerm_postgresql_flexible_server.main[0].administrator_login
  }, null)
}

output "postgresql_secret_name" {
  description = "Key Vault secret name containing PostgreSQL admin password"
  value       = try(azurerm_key_vault_secret.postgresql_password[0].name, null)
}

output "redis" {
  description = "Azure Managed Redis for OSMO (if deployed)."
  value = try({
    id       = azurerm_managed_redis.main[0].id
    hostname = azurerm_managed_redis.main[0].hostname
    name     = azurerm_managed_redis.main[0].name
    port     = azurerm_managed_redis.main[0].default_database[0].port
  }, null)
}

output "redis_secret_name" {
  description = "Key Vault secret name containing Redis primary access key"
  value       = try(azurerm_key_vault_secret.redis_primary_key[0].name, null)
}

output "osmo_workload_identity" {
  description = "OSMO workload identity for federated credentials"
  value = var.should_enable_osmo_identity ? {
    id           = azurerm_user_assigned_identity.osmo[0].id
    principal_id = azurerm_user_assigned_identity.osmo[0].principal_id
    client_id    = azurerm_user_assigned_identity.osmo[0].client_id
    tenant_id    = azurerm_user_assigned_identity.osmo[0].tenant_id
  } : null
}
