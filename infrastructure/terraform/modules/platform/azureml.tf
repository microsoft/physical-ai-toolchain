/**
 * # Azure Machine Learning Workspace
 *
 * This file creates the Azure ML Workspace for the Platform module including:
 * - Azure Machine Learning Workspace linked to Key Vault, Storage, ACR, App Insights
 * - Private endpoint for ML workspace
 *
 * Note: ML Extension, Kubernetes Compute, and FICs are in the SiL module (require AKS cluster)
 */

// ============================================================
// Azure Machine Learning Workspace (via azapi)
// ============================================================
// Using azapi_resource because azurerm does not expose systemDatastoresAuthMode.
// This property is required when storage account has shared_access_key_enabled = false.

resource "azapi_resource" "ml_workspace" {
  type      = "Microsoft.MachineLearningServices/workspaces@2024-04-01"
  name      = "mlw-${local.resource_name_suffix}"
  location  = var.resource_group.location
  parent_id = var.resource_group.id

  // Disable schema validation because azapi provider schema doesn't include
  // systemDatastoresAuthMode property, but it's valid per Microsoft ARM docs.
  schema_validation_enabled = false

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name = "Basic"
      tier = "Basic"
    }
    kind = "Default"
    properties = {
      friendlyName             = "mlw-${local.resource_name_suffix}"
      keyVault                 = azurerm_key_vault.main.id
      storageAccount           = azurerm_storage_account.main.id
      containerRegistry        = azurerm_container_registry.main.id
      applicationInsights      = azurerm_application_insights.main.id
      publicNetworkAccess      = var.should_enable_public_network_access ? "Enabled" : "Disabled"
      v1LegacyMode             = false
      systemDatastoresAuthMode = var.should_enable_storage_shared_access_key ? "accessKey" : "identity"
      managedNetwork = {
        isolationMode = var.aml_managed_network_isolation_mode
      }
    }
  }

  response_export_values = ["properties.workspaceId", "identity.principalId"]

  lifecycle {
    ignore_changes = [
      // ARM API returns resource provider segments with varying casing
      // (e.g. Microsoft.insights vs Microsoft.Insights) causing perpetual drift
      body.properties.applicationInsights,
      body.properties.keyVault,
    ]
  }
}

// ============================================================
// ML Workspace Private Endpoints
// ============================================================

resource "azurerm_private_endpoint" "azureml_api" {
  count = local.pe_enabled ? 1 : 0

  name                = "pe-ml-api-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-ml-api-${local.resource_name_suffix}"
    private_connection_resource_id = azapi_resource.ml_workspace.id
    subresource_names              = ["amlworkspace"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "pdz-ml-${local.resource_name_suffix}"
    private_dns_zone_ids = [
      azurerm_private_dns_zone.core["azureml_api"].id,
      azurerm_private_dns_zone.core["azureml_notebooks"].id,
    ]
  }
}

resource "azurerm_monitor_diagnostic_setting" "ml_workspace_logs" {
  count = var.should_enable_aml_diagnostic_logs ? 1 : 0

  name                       = "diag-mlw-${local.resource_name_suffix}"
  target_resource_id         = azapi_resource.ml_workspace.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

locals {
  should_attach_aml_compute_to_customer_subnet = var.aml_managed_network_isolation_mode == "Disabled"
  aml_user_assigned_identity_id                = azurerm_user_assigned_identity.ml.id
  aml_compute_clusters_normalized = {
    for cluster_name, cluster in var.aml_compute_clusters : cluster_name => {
      vm_size                   = cluster.vm_size
      vm_priority               = cluster.vm_priority
      min_node_count            = cluster.min_node_count
      max_node_count            = cluster.max_node_count
      scale_down_after_idle     = cluster.scale_down_after_idle
      subnet_id                 = cluster.subnet_id
      node_public_ip_enabled    = coalesce(cluster.node_public_ip_enabled, false)
      ssh_public_access_enabled = coalesce(cluster.ssh_public_access_enabled, false)
      identity_type             = coalesce(cluster.identity_type, "UserAssigned")
      identity_ids              = coalesce(cluster.identity_type, "UserAssigned") == "UserAssigned" ? [local.aml_user_assigned_identity_id] : null
      location                  = coalesce(cluster.location, var.resource_group.location)
    }
  }
}

// ============================================================
// AzureML Managed Compute Clusters
// ============================================================

resource "azurerm_machine_learning_compute_cluster" "gpu" {
  for_each = local.aml_compute_clusters_normalized

  name                          = each.key
  machine_learning_workspace_id = azapi_resource.ml_workspace.id
  location                      = each.value.location
  vm_size                       = each.value.vm_size
  vm_priority                   = each.value.vm_priority
  node_public_ip_enabled        = each.value.node_public_ip_enabled
  ssh_public_access_enabled     = each.value.ssh_public_access_enabled

  identity {
    type         = each.value.identity_type
    identity_ids = each.value.identity_ids
  }

  scale_settings {
    min_node_count                       = each.value.min_node_count
    max_node_count                       = each.value.max_node_count
    scale_down_nodes_after_idle_duration = each.value.scale_down_after_idle
  }

  subnet_resource_id = local.should_attach_aml_compute_to_customer_subnet ? coalesce(each.value.subnet_id, azurerm_subnet.main.id) : null

  lifecycle {
    precondition {
      condition     = local.should_attach_aml_compute_to_customer_subnet || each.value.subnet_id == null
      error_message = "aml_compute_clusters subnet_id can only be set when aml_managed_network_isolation_mode is Disabled."
    }
  }
}
