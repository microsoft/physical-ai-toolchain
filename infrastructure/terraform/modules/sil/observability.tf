/**
 * # AKS Observability Resources
 *
 * This file creates the AKS-specific observability infrastructure for the SiL module including:
 * - Data Collection Rule for Container Insights logs
 * - Data Collection Rule for Prometheus metrics
 * - Data Collection Rule associations with AKS cluster
 *
 * Note: Shared observability resources (LAW, Monitor Workspace, DCE) are provided by the platform module.
 */

// ============================================================
// Data Collection Rules
// ============================================================

// DCR for AKS Container Insights Logs (requires DCE)
resource "azurerm_monitor_data_collection_rule" "logs" {
  count = var.should_deploy_dce ? 1 : 0

  name                        = "dcr-logs-${local.resource_name_suffix}"
  location                    = var.resource_group.location
  resource_group_name         = var.resource_group.name
  data_collection_endpoint_id = var.data_collection_endpoint.id
  kind                        = "Linux"

  destinations {
    log_analytics {
      workspace_resource_id = var.log_analytics_workspace.id
      name                  = "destination-log"
    }
  }

  data_flow {
    streams      = ["Microsoft-ContainerLog", "Microsoft-ContainerLogV2", "Microsoft-KubeEvents", "Microsoft-KubePodInventory"]
    destinations = ["destination-log"]
  }

  data_sources {
    extension {
      name           = "ContainerInsightsExtension"
      extension_name = "ContainerInsights"
      streams        = ["Microsoft-ContainerLog", "Microsoft-ContainerLogV2", "Microsoft-KubeEvents", "Microsoft-KubePodInventory"]

      extension_json = jsonencode({
        dataCollectionSettings = {
          interval               = "1m"
          namespaceFilteringMode = "Off"
          enableContainerLogV2   = true
        }
      })
    }
  }
}

// DCR for AKS Prometheus Metrics (requires DCE and Monitor Workspace)
resource "azurerm_monitor_data_collection_rule" "metrics" {
  count = var.should_deploy_dce && var.should_deploy_monitor_workspace ? 1 : 0

  name                        = "dcr-metrics-${local.resource_name_suffix}"
  location                    = var.resource_group.location
  resource_group_name         = var.resource_group.name
  data_collection_endpoint_id = var.data_collection_endpoint.id
  kind                        = "Linux"

  destinations {
    monitor_account {
      monitor_account_id = var.monitor_workspace.id
      name               = "destination-metrics"
    }
  }

  data_flow {
    streams      = ["Microsoft-PrometheusMetrics"]
    destinations = ["destination-metrics"]
  }

  data_sources {
    prometheus_forwarder {
      name    = "PrometheusDataSource"
      streams = ["Microsoft-PrometheusMetrics"]
    }
  }
}

// ============================================================
// Data Collection Rule Associations
// ============================================================

// Associate Container Insights logs DCR with AKS
resource "azurerm_monitor_data_collection_rule_association" "logs" {
  count = var.should_deploy_dce ? 1 : 0

  name                    = "dcra-logs-${local.resource_name_suffix}"
  target_resource_id      = azurerm_kubernetes_cluster.main.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.logs[0].id
}

// Associate Prometheus metrics DCR with AKS
resource "azurerm_monitor_data_collection_rule_association" "metrics" {
  count = var.should_deploy_dce && var.should_deploy_monitor_workspace ? 1 : 0

  name                    = "dcra-metrics-${local.resource_name_suffix}"
  target_resource_id      = azurerm_kubernetes_cluster.main.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.metrics[0].id
}

// ============================================================
// Data Collection Endpoint Association
// ============================================================

// Required for Container Insights MSI authentication mode
resource "azurerm_monitor_data_collection_rule_association" "dce" {
  count = var.should_deploy_dce ? 1 : 0

  target_resource_id          = azurerm_kubernetes_cluster.main.id
  data_collection_endpoint_id = var.data_collection_endpoint.id
}
