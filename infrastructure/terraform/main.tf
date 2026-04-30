/**
 * # Robotics Blueprint
 *
 * Deploys robotics infrastructure with NVIDIA GPU support, KAI Scheduler,
 * and optional Azure Machine Learning integration.
 *
 * Architecture:
 * - Platform Module: Shared services (networking, security, observability, ACR, storage, ML workspace)
 * - SiL Module: AKS cluster with GPU node pools and ML extension integration
 */

locals {
  resource_group_name = coalesce(var.resource_group_name, "rg-${var.resource_prefix}-${var.environment}-${var.instance}")
  current_user_oid    = try(msgraph_resource_action.current_user[0].output.oid, null)
}

resource "msgraph_resource_action" "current_user" {
  count = var.should_add_current_user_key_vault_admin ? 1 : 0

  method       = "GET"
  resource_url = "me"

  response_export_values = {
    oid = "id"
  }
}

resource "azurerm_resource_group" "this" {
  count    = var.should_create_resource_group ? 1 : 0
  name     = local.resource_group_name
  location = var.location
  tags     = var.tags
}

// Defer resource group data source to support build systems without plan-time permissions
resource "terraform_data" "defer_resource_group" {
  count = var.should_create_resource_group ? 0 : 1
  input = {
    name = local.resource_group_name
  }
}

data "azurerm_resource_group" "existing" {
  count = var.should_create_resource_group ? 0 : 1
  name  = terraform_data.defer_resource_group[0].output.name
}

locals {
  // Resolve resource group to either created or existing
  resource_group = var.should_create_resource_group ? {
    id       = azurerm_resource_group.this[0].id
    name     = azurerm_resource_group.this[0].name
    location = azurerm_resource_group.this[0].location
    } : {
    id       = data.azurerm_resource_group.existing[0].id
    name     = data.azurerm_resource_group.existing[0].name
    location = data.azurerm_resource_group.existing[0].location
  }
}

// ============================================================
// Platform Module - Shared Services
// ============================================================

module "platform" {
  source = "./modules/platform"

  depends_on = [azurerm_resource_group.this]

  // Core variables
  environment     = var.environment
  resource_prefix = var.resource_prefix
  location        = var.location
  instance        = var.instance
  resource_group  = local.resource_group

  // Current user OID for role assignments (from Microsoft Graph)
  current_user_oid = local.current_user_oid

  // Networking configuration
  should_enable_nat_gateway = var.should_enable_nat_gateway
  nat_gateway_zones         = var.nat_gateway_zones
  should_create_vm_subnet   = var.should_create_vm_subnet
  virtual_network_config = {
    address_space                  = var.virtual_network_config.address_space
    subnet_address_prefix_main     = var.virtual_network_config.subnet_address_prefix
    subnet_address_prefix_vm       = var.virtual_network_config.subnet_address_prefix_vm
    subnet_address_prefix_pe       = var.virtual_network_config.subnet_address_prefix_pe
    subnet_address_prefix_resolver = var.virtual_network_config.subnet_address_prefix_resolver
  }

  // Feature flags
  should_enable_private_endpoint          = var.should_enable_private_endpoint
  should_enable_public_network_access     = var.should_enable_public_network_access
  should_add_current_user_key_vault_admin = var.should_add_current_user_key_vault_admin
  should_add_current_user_storage_blob    = var.should_add_current_user_storage_blob
  should_enable_purge_protection          = var.should_enable_purge_protection
  should_create_data_lake_storage         = var.should_create_data_lake_storage

  // Storage lifecycle management
  should_enable_raw_bags_lifecycle_policy           = var.should_enable_raw_bags_lifecycle_policy
  raw_bags_retention_days                           = var.raw_bags_retention_days
  should_enable_converted_datasets_lifecycle_policy = var.should_enable_converted_datasets_lifecycle_policy
  converted_datasets_cool_tier_days                 = var.converted_datasets_cool_tier_days
  should_enable_reports_lifecycle_policy            = var.should_enable_reports_lifecycle_policy
  reports_cool_tier_days                            = var.reports_cool_tier_days
  reports_archive_tier_days                         = var.reports_archive_tier_days

  // OSMO services
  should_create_osmo_secret = var.osmo_config.should_create_secret
  should_deploy_postgresql  = var.should_deploy_postgresql
  should_deploy_redis       = var.should_deploy_redis
  postgresql_config = {
    location                        = coalesce(var.postgresql_location, var.location)
    sku_name                        = var.postgresql_sku_name
    storage_mb                      = var.postgresql_storage_mb
    version                         = var.postgresql_version
    databases                       = var.postgresql_databases
    zone                            = var.postgresql_zone
    should_enable_high_availability = var.postgresql_high_availability.should_enable
    standby_availability_zone       = var.postgresql_high_availability.standby_availability_zone
  }
  redis_config = {
    sku_name                        = var.redis_sku_name
    clustering_policy               = var.redis_clustering_policy
    should_enable_high_availability = var.should_enable_redis_high_availability
  }

  // OSMO workload identity
  should_enable_osmo_identity = var.osmo_config.should_enable_identity

  // Observability feature flags
  should_deploy_grafana           = var.should_deploy_grafana
  should_deploy_monitor_workspace = var.should_deploy_monitor_workspace
  should_deploy_ampls             = var.should_deploy_ampls
  should_deploy_dce               = var.should_deploy_dce

  // AzureML compute
  should_enable_aml_diagnostic_logs = var.should_enable_aml_diagnostic_logs
  should_deploy_aml_compute         = var.should_deploy_aml_compute
  aml_compute_config                = var.aml_compute_config

  // DNS zone flags
  should_include_aks_dns_zone = var.should_include_aks_dns_zone
}

// ============================================================
// SiL Module - AKS + AzureML Extension
// ============================================================

module "sil" {
  source = "./modules/sil"

  depends_on = [module.platform]

  // Core variables
  environment     = var.environment
  resource_prefix = var.resource_prefix
  instance        = var.instance
  location        = var.location
  resource_group  = local.resource_group

  // Current user OID for cluster admin role assignments (from Microsoft Graph)
  current_user_oid = local.current_user_oid

  // Dependencies from platform module (passed as typed objects)
  virtual_network                 = module.platform.virtual_network
  subnets                         = module.platform.subnets
  network_security_group          = module.platform.network_security_group
  nat_gateway                     = module.platform.nat_gateway
  should_enable_nat_gateway       = var.should_enable_nat_gateway
  log_analytics_workspace         = module.platform.log_analytics_workspace
  monitor_workspace               = module.platform.monitor_workspace
  data_collection_endpoint        = module.platform.data_collection_endpoint
  container_registry              = module.platform.container_registry
  private_dns_zones               = module.platform.private_dns_zones
  should_deploy_monitor_workspace = var.should_deploy_monitor_workspace
  should_deploy_dce               = var.should_deploy_dce

  // AKS subnet configuration - uses module defaults when null
  aks_subnet_config = {
    subnet_address_prefix_aks     = try(var.subnet_address_prefixes_aks[0], null)
    subnet_address_prefix_aks_pod = try(var.subnet_address_prefixes_aks_pod[0], null)
  }

  // AKS system node pool configuration
  aks_config = {
    system_node_pool_vm_size                    = var.system_node_pool_vm_size
    system_node_pool_node_count                 = var.system_node_pool_node_count
    should_enable_system_node_pool_auto_scaling = var.should_enable_system_node_pool_auto_scaling
    system_node_pool_min_count                  = var.system_node_pool_min_count
    system_node_pool_max_count                  = var.system_node_pool_max_count
    should_enable_private_cluster               = var.should_enable_private_aks_cluster
    system_node_pool_zones                      = var.system_node_pool_zones
    should_enable_microsoft_defender            = var.should_enable_microsoft_defender
  }

  node_pools = var.node_pools

  // OSMO workload identity
  osmo_workload_identity = module.platform.osmo_workload_identity
  osmo_config = {
    should_federate_identity = var.osmo_config.should_federate_identity
    control_plane_namespace  = var.osmo_config.control_plane_namespace
    operator_namespace       = var.osmo_config.operator_namespace
    workflows_namespace      = var.osmo_config.workflows_namespace
  }

  // Feature flags
  should_enable_private_endpoint = var.should_enable_private_endpoint
}

// ============================================================
// Container Supply-Chain Security
// ============================================================

module "github_oidc" {
  count  = var.signing_mode != "none" ? 1 : 0
  source = "./modules/github-oidc"

  environment     = var.environment
  resource_prefix = var.resource_prefix
  instance        = var.instance
  location        = var.location
  resource_group  = local.resource_group

  github_owner = var.github_repository_owner
  github_repo  = var.github_repository_name
  federated_subjects = {
    tags-publish = "repo:${var.github_repository_owner}/${var.github_repository_name}:ref:refs/tags/v*"
  }

  acr = module.platform.container_registry
}

module "notation_akv" {
  count  = var.signing_mode == "notation" ? 1 : 0
  source = "./modules/notation-akv"

  environment     = var.environment
  resource_prefix = var.resource_prefix
  instance        = var.instance
  location        = var.location
  resource_group  = local.resource_group

  should_deploy         = true
  signer_subject_claims = ["system:serviceaccount:arc-runners:notation-signer"]

  aks = {
    id              = module.sil.aks_cluster.id
    oidc_issuer_url = module.sil.aks_oidc_issuer_url
  }
  acr = {
    id           = module.platform.container_registry.id
    login_server = module.platform.container_registry.login_server
  }
  key_vault = {
    id        = module.platform.key_vault.id
    vault_uri = module.platform.key_vault.vault_uri
  }
  github_oidc = length(module.github_oidc) > 0 ? {
    uami_id           = module.github_oidc[0].user_assigned_identity.id
    uami_client_id    = module.github_oidc[0].client_id
    uami_principal_id = module.github_oidc[0].principal_id
  } : null
}

module "arc_runners" {
  count  = var.signing_mode != "none" ? 1 : 0
  source = "./modules/arc-runners"

  environment     = var.environment
  resource_prefix = var.resource_prefix
  instance        = var.instance
  location        = var.location
  resource_group  = local.resource_group

  github_config_url                = "https://github.com/${var.github_repository_owner}/${var.github_repository_name}"
  github_app_id                    = var.github_app_id
  github_app_installation_id       = var.github_app_installation_id
  github_app_private_key_secret_id = var.github_app_private_key_secret_id
  should_enable_sigstore_egress    = var.signing_mode == "sigstore"

  aks = {
    id                     = module.sil.aks_cluster.id
    oidc_issuer_url        = module.sil.aks_oidc_issuer_url
    host                   = module.sil.aks_kube_config.host
    cluster_ca_certificate = module.sil.aks_kube_config.cluster_ca_certificate
    kube_config_raw        = module.sil.aks_kube_config.kube_config_raw
  }
  acr = {
    id           = module.platform.container_registry.id
    login_server = module.platform.container_registry.login_server
  }
  key_vault = {
    id        = module.platform.key_vault.id
    vault_uri = module.platform.key_vault.vault_uri
  }
  github_oidc = length(module.github_oidc) > 0 ? {
    uami_id           = module.github_oidc[0].user_assigned_identity.id
    uami_client_id    = module.github_oidc[0].client_id
    uami_principal_id = module.github_oidc[0].principal_id
  } : null
}

module "sigstore_mirror" {
  count  = (var.signing_mode == "sigstore" && var.should_deploy_sigstore_mirror) ? 1 : 0
  source = "./modules/sigstore-mirror"

  environment     = var.environment
  resource_prefix = var.resource_prefix
  instance        = var.instance
  location        = var.location
  resource_group  = local.resource_group

  should_deploy = true
}
