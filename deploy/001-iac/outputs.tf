/**
 * # Robotics Blueprint Outputs
 *
 * Outputs organized by consumption:
 * - 002-setup scripts: AKS cluster info, OSMO connection details, Key Vault name
 * - Platform module: Shared infrastructure (networking, security, observability)
 * - SiL module: AKS cluster and ML extension resources
 */

// ============================================================
// Core Outputs
// ============================================================

output "resource_group" {
  description = "Resource group for robotics infrastructure."
  value       = local.resource_group
}

// ============================================================
// Security Outputs
// ============================================================

output "key_vault" {
  description = "Key Vault storing robotics secrets."
  value       = module.platform.key_vault
}

output "key_vault_name" {
  description = "Key Vault name for script consumption."
  value       = module.platform.key_vault.name
}

// ============================================================
// AKS Cluster Outputs
// ============================================================

output "aks_cluster" {
  description = "AKS cluster for robotics workloads."
  value       = module.sil.aks_cluster
}

output "aks_oidc_issuer_url" {
  description = "OIDC issuer URL for workload identity."
  value       = module.sil.aks_oidc_issuer_url
}

output "gpu_node_pool_subnets" {
  description = "GPU node pool subnets created by SiL module."
  value       = module.sil.gpu_node_pool_subnets
}

output "node_pools" {
  description = "GPU node pool configurations for OSMO pool and pod template generation"
  value       = module.sil.node_pools
}

// ============================================================
// ML Workspace Outputs
// ============================================================

output "azureml_workspace" {
  description = "Azure ML workspace for ML workloads."
  value       = module.platform.azureml_workspace
}

output "ml_workload_identity" {
  description = "ML workload identity for federated credentials."
  value       = module.platform.ml_workload_identity
}

// ============================================================
// OSMO Connection Outputs (for deploy-osmo-control-plane.sh)
// ============================================================

output "postgresql_connection_info" {
  description = "PostgreSQL connection information for OSMO control plane."
  value = module.platform.postgresql != null ? {
    fqdn           = module.platform.postgresql.fqdn
    name           = module.platform.postgresql.name
    admin_username = module.platform.postgresql.admin_username
    secret_name    = module.platform.postgresql_secret_name
  } : null
}

output "managed_redis_connection_info" {
  description = "Redis connection information for OSMO control plane."
  value = module.platform.redis != null ? {
    hostname    = module.platform.redis.hostname
    name        = module.platform.redis.name
    port        = module.platform.redis.port
    secret_name = module.platform.redis_secret_name
  } : null
}

// ============================================================
// Networking Outputs
// ============================================================

output "virtual_network" {
  description = "Virtual network for robotics infrastructure."
  value       = module.platform.virtual_network
}

output "subnets" {
  description = "Subnet details from platform module."
  value       = module.platform.subnets
}

// ============================================================
// DNS Private Resolver Outputs
// ============================================================

output "private_dns_resolver" {
  description = "Private DNS Resolver for resolving private DNS zones from VPN clients or on-premises networks."
  value       = module.platform.private_dns_resolver
}

output "dns_server_ip" {
  description = "The IP address to use as DNS server for VPN clients or on-premises DNS forwarding."
  value       = module.platform.dns_server_ip
}

// ============================================================
// Compute Resources Outputs
// ============================================================

output "container_registry" {
  description = "Azure Container Registry for container images."
  value       = module.platform.container_registry
}

output "storage_account" {
  description = "Storage account for ML workspace and general storage."
  value       = module.platform.storage_account
}

// ============================================================
// AzureML Compute Outputs
// ============================================================

output "aml_compute_cluster" {
  description = "AzureML managed compute cluster. Null when compute deployment is disabled."
  value       = module.platform.aml_compute_cluster
}

// ============================================================
// Observability Outputs
// ============================================================

output "log_analytics_workspace" {
  description = "Log Analytics Workspace for centralized logging."
  value       = module.platform.log_analytics_workspace
}

output "application_insights" {
  description = "Application Insights for application telemetry."
  value       = module.platform.application_insights
  sensitive   = true
}

output "grafana" {
  description = "Azure Managed Grafana for dashboards."
  value       = module.platform.grafana
}

// ============================================================
// OSMO Services Outputs (Optional)
// ============================================================

output "postgresql" {
  description = "PostgreSQL Flexible Server object."
  value       = module.platform.postgresql
}

output "redis" {
  description = "Azure Redis Cache object."
  value       = module.platform.redis
}

output "osmo_workload_identity" {
  description = "OSMO workload identity for deployment scripts"
  value       = module.platform.osmo_workload_identity
}

// ============================================================
// Dataviewer Outputs (Optional)
// ============================================================

output "dataviewer" {
  description = "Dataviewer Container Apps deployment details. Null when dataviewer is not deployed."
  value = var.should_deploy_dataviewer ? {
    environment = module.dataviewer[0].container_app_environment
    backend     = module.dataviewer[0].backend
    frontend    = module.dataviewer[0].frontend
    identity    = module.dataviewer[0].dataviewer_identity
    entra_id    = module.dataviewer[0].entra_id
  } : null
}
