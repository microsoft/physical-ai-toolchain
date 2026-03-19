// SIL module naming convention tests
// Validates resource names follow {abbreviation}-{prefix}-{env}-{instance} convention

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "tls" {}
mock_provider "random" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "verify_naming_conventions" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    virtual_network                = run.setup.virtual_network
    subnets                        = run.setup.subnets
    network_security_group         = run.setup.network_security_group
    nat_gateway                    = run.setup.nat_gateway
    log_analytics_workspace        = run.setup.log_analytics_workspace
    container_registry             = run.setup.container_registry
    private_dns_zones              = run.setup.private_dns_zones
    monitor_workspace              = run.setup.monitor_workspace
    data_collection_endpoint       = run.setup.data_collection_endpoint
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
    osmo_workload_identity         = run.setup.osmo_workload_identity
    osmo_config = {
      should_federate_identity = true
      control_plane_namespace  = "osmo-control-plane"
      operator_namespace       = "osmo-operator"
      workflows_namespace      = "osmo-workflows"
    }
  }

  // AKS Managed Identity
  assert {
    condition     = azurerm_user_assigned_identity.aks.name == "id-aks-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "AKS identity name must follow id-aks-{prefix}-{env}-{instance}"
  }

  // AKS Cluster
  assert {
    condition     = azurerm_kubernetes_cluster.main.name == "aks-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "AKS cluster name must follow aks-{prefix}-{env}-{instance}"
  }

  // AKS DNS prefix (no instance — intentional)
  assert {
    condition     = azurerm_kubernetes_cluster.main.dns_prefix == "aks-${run.setup.resource_prefix}-${run.setup.environment}"
    error_message = "AKS DNS prefix must follow aks-{prefix}-{env} (no instance)"
  }

  // AKS system node pool subnet
  assert {
    condition     = azurerm_subnet.aks.name == "snet-aks-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "AKS subnet name must follow snet-aks-{prefix}-{env}-{instance}"
  }

  // GPU node pool subnet (default 'gpu' pool)
  assert {
    condition     = azurerm_subnet.gpu_node_pool["gpu"].name == "snet-aks-gpu-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "GPU subnet name must follow snet-aks-{pool_name}-{prefix}-{env}-{instance}"
  }

  // Private endpoint name
  assert {
    condition     = azurerm_private_endpoint.aks[0].name == "pe-aks-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "AKS PE name must follow pe-aks-{prefix}-{env}-{instance}"
  }

  // DCR logs name
  assert {
    condition     = azurerm_monitor_data_collection_rule.logs[0].name == "dcr-logs-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Logs DCR name must follow dcr-logs-{prefix}-{env}-{instance}"
  }

  // DCR metrics name
  assert {
    condition     = azurerm_monitor_data_collection_rule.metrics[0].name == "dcr-metrics-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Metrics DCR name must follow dcr-metrics-{prefix}-{env}-{instance}"
  }

  // DCRA logs name
  assert {
    condition     = azurerm_monitor_data_collection_rule_association.logs[0].name == "dcra-logs-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Logs DCRA name must follow dcra-logs-{prefix}-{env}-{instance}"
  }

  // DCRA metrics name
  assert {
    condition     = azurerm_monitor_data_collection_rule_association.metrics[0].name == "dcra-metrics-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Metrics DCRA name must follow dcra-metrics-{prefix}-{env}-{instance}"
  }

  // OSMO FIC naming (non-standard, key-based)
  assert {
    condition     = azurerm_federated_identity_credential.osmo["osmo-control-plane"].name == "osmo-osmo-control-plane-fic"
    error_message = "OSMO FIC name must follow osmo-{key}-fic"
  }

  // OSMO default SA FIC naming
  assert {
    condition     = azurerm_federated_identity_credential.osmo_default_sa["osmo-control-plane"].name == "osmo-osmo-control-plane-default-sa-fic"
    error_message = "OSMO default SA FIC name must follow osmo-{namespace}-default-sa-fic"
  }
}
