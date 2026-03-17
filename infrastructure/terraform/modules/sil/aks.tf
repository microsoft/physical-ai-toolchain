/**
 * # AKS Cluster Resources
 *
 * This file creates the Azure Kubernetes Service cluster for the SiL module including:
 * - User Assigned Managed Identity (required for custom private DNS zone)
 * - AKS cluster with Azure CNI Overlay networking
 * - System node pool for core workloads
 * - GPU node pools via for_each (configurable)
 * - Workload identity and OIDC issuer enabled
 * - Private endpoint for private clusters
 *
 * Note: Networking resources are in networking.tf
 * Note: Observability resources are in observability.tf
 * Note: Role assignments are in role-assignments.tf
 */

// ============================================================
// AKS User Assigned Managed Identity
// ============================================================
// Required when using a custom private DNS zone. The identity must exist
// and have DNS Zone Contributor role BEFORE the AKS cluster is created.

resource "azurerm_user_assigned_identity" "aks" {
  name                = "id-aks-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
}

// ============================================================
// AKS Cluster
// ============================================================

resource "azurerm_kubernetes_cluster" "main" {
  name                              = "aks-${local.resource_name_suffix}"
  location                          = var.resource_group.location
  resource_group_name               = var.resource_group.name
  dns_prefix                        = "aks-${var.resource_prefix}-${var.environment}"
  kubernetes_version                = null // Use latest stable version
  automatic_upgrade_channel         = "patch"
  sku_tier                          = "Standard"
  private_cluster_enabled           = var.aks_config.is_private_cluster
  private_dns_zone_id               = var.aks_config.is_private_cluster && local.pe_enabled ? var.private_dns_zones["aks"].id : null
  local_account_disabled            = true
  azure_policy_enabled              = true
  oidc_issuer_enabled               = true
  workload_identity_enabled         = true
  role_based_access_control_enabled = true
  node_os_upgrade_channel           = "NodeImage"

  default_node_pool {
    name                        = "system"
    vm_size                     = var.aks_config.system_node_pool_vm_size
    node_count                  = var.aks_config.system_node_pool_enable_auto_scaling ? null : var.aks_config.system_node_pool_node_count
    auto_scaling_enabled        = var.aks_config.system_node_pool_enable_auto_scaling
    min_count                   = var.aks_config.system_node_pool_enable_auto_scaling ? var.aks_config.system_node_pool_min_count : null
    max_count                   = var.aks_config.system_node_pool_enable_auto_scaling ? var.aks_config.system_node_pool_max_count : null
    vnet_subnet_id              = azurerm_subnet.aks.id
    os_disk_size_gb             = 128
    os_disk_type                = "Ephemeral"
    temporary_name_for_rotation = "systemtemp"
    zones                       = var.aks_config.system_node_pool_zones

    upgrade_settings {
      max_surge                     = "10%"
      drain_timeout_in_minutes      = 0
      node_soak_duration_in_minutes = 0
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.aks.id]
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_policy      = "azure"
    outbound_type       = "userAssignedNATGateway"
    service_cidr        = "172.16.0.0/16"
    dns_service_ip      = "172.16.0.10"
    pod_cidr            = "10.244.0.0/16"
    load_balancer_sku   = "standard"
  }

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled     = true
    admin_group_object_ids = []
  }

  dynamic "microsoft_defender" {
    for_each = var.aks_config.should_enable_microsoft_defender ? [1] : []
    content {
      log_analytics_workspace_id = var.log_analytics_workspace.id
    }
  }

  oms_agent {
    log_analytics_workspace_id      = var.log_analytics_workspace.id
    msi_auth_for_monitoring_enabled = true
  }

  monitor_metrics {
    annotations_allowed = null
    labels_allowed      = null
  }

  key_vault_secrets_provider {
    secret_rotation_enabled  = true
    secret_rotation_interval = "2m"
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.aks,
    azurerm_role_assignment.aks_dns_zone_contributor,
  ]
}

// ============================================================
// GPU Node Pools
// ============================================================

resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
  for_each = var.node_pools

  name                  = each.key
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  node_count            = each.value.node_count
  vm_size               = each.value.vm_size
  vnet_subnet_id        = azurerm_subnet.gpu_node_pool[each.key].id
  node_taints           = each.value.node_taints
  auto_scaling_enabled  = each.value.enable_auto_scaling
  min_count             = each.value.enable_auto_scaling ? each.value.min_count : null
  max_count             = each.value.enable_auto_scaling ? each.value.max_count : null
  priority              = each.value.priority
  zones                 = each.value.zones
  eviction_policy       = each.value.priority == "Spot" ? each.value.eviction_policy : null
  gpu_driver            = each.value.gpu_driver
  node_labels           = each.value.node_labels

  // Spot pools do not support upgrade_settings (Azure rejects maxUnavailable for Spot)
  dynamic "upgrade_settings" {
    for_each = each.value.priority != "Spot" ? [1] : []
    content {
      max_surge                     = "10%"
      drain_timeout_in_minutes      = 0
      node_soak_duration_in_minutes = 0
    }
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.gpu_node_pool,
  ]
}

// ============================================================
// AKS Private Endpoint (for private clusters)
// ============================================================

resource "azurerm_private_endpoint" "aks" {
  // Use known boolean values for count to avoid plan-time dependency issues
  // pe_enabled ensures the PE subnet exists when this resource is created
  count = var.aks_config.is_private_cluster && local.pe_enabled ? 1 : 0

  name                = "pe-aks-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = var.subnets.private_endpoints.id

  private_service_connection {
    name                           = "psc-aks-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_kubernetes_cluster.main.id
    subresource_names              = ["management"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-aks-${local.resource_name_suffix}"
    private_dns_zone_ids = [var.private_dns_zones["aks"].id]
  }
}
