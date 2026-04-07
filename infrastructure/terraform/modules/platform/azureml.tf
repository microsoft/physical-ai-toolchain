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
        isolationMode = var.should_enable_private_endpoint ? "AllowOnlyApprovedOutbound" : "Disabled"
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

  metric {
    category = "AllMetrics"
    enabled  = false
  }
}

// ============================================================
// AzureML Managed Compute Cluster (Optional)
// ============================================================

resource "azurerm_machine_learning_compute_cluster" "gpu" {
  count = var.should_deploy_aml_compute ? 1 : 0

  name                          = var.aml_compute_config.cluster_name
  machine_learning_workspace_id = azapi_resource.ml_workspace.id
  location                      = var.resource_group.location
  vm_size                       = var.aml_compute_config.vm_size
  vm_priority                   = var.aml_compute_config.vm_priority

  identity {
    type = "SystemAssigned"
  }

  scale_settings {
    min_node_count                       = var.aml_compute_config.min_node_count
    max_node_count                       = var.aml_compute_config.max_node_count
    scale_down_nodes_after_idle_duration = var.aml_compute_config.scale_down_after_idle
  }

  // Custom subnet is incompatible with workspace managed VNet (AllowOnlyApprovedOutbound).
  // Only set subnet_resource_id when workspace does NOT use managed VNet isolation.
  subnet_resource_id = var.should_enable_private_endpoint ? null : coalesce(var.aml_compute_config.subnet_id, azurerm_subnet.main.id)
}
