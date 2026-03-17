/**
 * # Observability Resources
 *
 * This file creates the observability stack for the Platform module including:
 * - Log Analytics Workspace for centralized logging (always deployed)
 * - Application Insights for application telemetry (always deployed)
 * - Azure Monitor Workspace for Prometheus metrics (optional)
 * - Azure Managed Grafana for dashboards (optional)
 * - Data Collection Endpoint (optional)
 * - Azure Monitor Private Link Scope (AMPLS) with private endpoint (optional)
 *
 * Note: AKS-specific Data Collection Rules (Container Insights, Prometheus) are in the SiL module
 * Note: AMPLS uses the SHARED storage_blob DNS zone from private-dns-zones.tf
 */

// ============================================================
// Log Analytics Workspace
// ============================================================

resource "azurerm_log_analytics_workspace" "main" {
  name                       = "log-${local.resource_name_suffix}"
  location                   = var.resource_group.location
  resource_group_name        = var.resource_group.name
  sku                        = "PerGB2018"
  retention_in_days          = 30
  internet_ingestion_enabled = var.should_enable_public_network_access
  internet_query_enabled     = var.should_enable_public_network_access
}

// ============================================================
// Application Insights
// ============================================================

resource "azurerm_application_insights" "main" {
  name                       = "ai-${local.resource_name_suffix}"
  location                   = var.resource_group.location
  resource_group_name        = var.resource_group.name
  workspace_id               = azurerm_log_analytics_workspace.main.id
  application_type           = "other"
  internet_ingestion_enabled = var.should_enable_public_network_access
  internet_query_enabled     = var.should_enable_public_network_access
}

// ============================================================
// Azure Monitor Workspace (Prometheus) - Optional
// ============================================================

resource "azurerm_monitor_workspace" "main" {
  count = var.should_deploy_monitor_workspace ? 1 : 0

  name                          = "azmon-${local.resource_name_suffix}"
  location                      = var.resource_group.location
  resource_group_name           = var.resource_group.name
  public_network_access_enabled = var.should_enable_public_network_access
}

// ============================================================
// Azure Managed Grafana - Optional
// ============================================================

resource "azurerm_dashboard_grafana" "main" {
  count = var.should_deploy_grafana ? 1 : 0

  name                              = "graf-${local.resource_name_suffix}"
  location                          = var.resource_group.location
  resource_group_name               = var.resource_group.name
  api_key_enabled                   = true
  deterministic_outbound_ip_enabled = false
  public_network_access_enabled     = var.should_enable_public_network_access
  grafana_major_version             = 11
  sku                               = "Standard"
  zone_redundancy_enabled           = false

  dynamic "azure_monitor_workspace_integrations" {
    for_each = var.should_deploy_monitor_workspace ? [1] : []
    content {
      resource_id = azurerm_monitor_workspace.main[0].id
    }
  }

  identity {
    type = "SystemAssigned"
  }
}

// ============================================================
// Data Collection Endpoints - Optional
// ============================================================

resource "azurerm_monitor_data_collection_endpoint" "main" {
  count = var.should_deploy_dce ? 1 : 0

  name                          = "dce-${local.resource_name_suffix}"
  location                      = var.resource_group.location
  resource_group_name           = var.resource_group.name
  kind                          = "Linux"
  public_network_access_enabled = var.should_enable_public_network_access
}

// ============================================================
// Azure Monitor Private Link Scope (AMPLS) - Optional
// ============================================================

resource "azurerm_monitor_private_link_scope" "main" {
  count = local.pe_enabled && var.should_deploy_ampls ? 1 : 0

  name                  = "ampls-${local.resource_name_suffix}"
  resource_group_name   = var.resource_group.name
  ingestion_access_mode = "Open"
  query_access_mode     = "PrivateOnly"
}

// Link Log Analytics Workspace to AMPLS
resource "azurerm_monitor_private_link_scoped_service" "law" {
  count = local.pe_enabled && var.should_deploy_ampls ? 1 : 0

  name                = "log-link"
  resource_group_name = var.resource_group.name
  scope_name          = azurerm_monitor_private_link_scope.main[0].name
  linked_resource_id  = azurerm_log_analytics_workspace.main.id
}

// Link Application Insights to AMPLS
resource "azurerm_monitor_private_link_scoped_service" "ai" {
  count = local.pe_enabled && var.should_deploy_ampls ? 1 : 0

  name                = "ai-link"
  resource_group_name = var.resource_group.name
  scope_name          = azurerm_monitor_private_link_scope.main[0].name
  linked_resource_id  = azurerm_application_insights.main.id
}

// Link Data Collection Endpoint to AMPLS
resource "azurerm_monitor_private_link_scoped_service" "dce" {
  count = local.pe_enabled && var.should_deploy_ampls && var.should_deploy_dce ? 1 : 0

  name                = "dce-link"
  resource_group_name = var.resource_group.name
  scope_name          = azurerm_monitor_private_link_scope.main[0].name
  linked_resource_id  = azurerm_monitor_data_collection_endpoint.main[0].id
}

// ============================================================
// AMPLS Private Endpoint - Optional
// ============================================================

resource "azurerm_private_endpoint" "monitor" {
  count = local.pe_enabled && var.should_deploy_ampls ? 1 : 0

  name                = "pe-monitor-${local.resource_name_suffix}"
  location            = var.resource_group.location
  resource_group_name = var.resource_group.name
  subnet_id           = azurerm_subnet.private_endpoints[0].id

  private_service_connection {
    name                           = "psc-monitor-${local.resource_name_suffix}"
    private_connection_resource_id = azurerm_monitor_private_link_scope.main[0].id
    subresource_names              = ["azuremonitor"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name = "pdz-monitor-${local.resource_name_suffix}"
    // AMPLS requires 5 DNS zones including the SHARED storage_blob zone
    private_dns_zone_ids = [
      azurerm_private_dns_zone.core["monitor"].id,
      azurerm_private_dns_zone.core["monitor_oms"].id,
      azurerm_private_dns_zone.core["monitor_ods"].id,
      azurerm_private_dns_zone.core["monitor_agent"].id,
      azurerm_private_dns_zone.core["storage_blob"].id, // SHARED with Storage Account
    ]
  }

  // Ensure all scoped services are linked before creating the private endpoint
  // to avoid "Mismatching RequiredMembers in Request" error
  depends_on = [
    azurerm_monitor_private_link_scoped_service.law,
    azurerm_monitor_private_link_scoped_service.ai,
    azurerm_monitor_private_link_scoped_service.dce,
  ]
}
