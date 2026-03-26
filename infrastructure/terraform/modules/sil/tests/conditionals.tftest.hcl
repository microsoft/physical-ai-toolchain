// SIL module conditional resource tests
// Validates should_* boolean and nullable dependency variables control resource creation

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "tls" {}
mock_provider "random" {}

variables {
  should_assign_cluster_admin     = false
  should_deploy_dce               = false
  should_deploy_monitor_workspace = false
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Private Endpoint Conditionals (4 combinations)
// ============================================================

run "pe_enabled_private_cluster_enabled" {
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
    should_enable_private_endpoint = true
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = true
    }
  }

  assert {
    condition     = length(azurerm_private_endpoint.aks) == 1
    error_message = "PE should be created when both PE and private cluster are enabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_dns_zone_contributor) == 1
    error_message = "DNS zone contributor role should be assigned for private cluster with PE"
  }
}

run "pe_enabled_private_cluster_disabled" {
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
    should_enable_private_endpoint = true
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_private_endpoint.aks) == 0
    error_message = "PE should not be created when private cluster is disabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_dns_zone_contributor) == 0
    error_message = "DNS zone contributor role should not be assigned when private cluster is disabled"
  }
}

run "pe_disabled_private_cluster_enabled" {
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
    should_enable_private_endpoint = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = true
    }
  }

  assert {
    condition     = length(azurerm_private_endpoint.aks) == 0
    error_message = "PE should not be created when PE is disabled even if private cluster is enabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_dns_zone_contributor) == 0
    error_message = "DNS zone contributor role should not be assigned when PE is disabled"
  }
}

run "pe_disabled_private_cluster_disabled" {
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
    should_enable_private_endpoint = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_private_endpoint.aks) == 0
    error_message = "PE should not be created when both PE and private cluster are disabled"
  }
}

// ============================================================
// NAT Gateway Association Conditionals
// ============================================================

run "nat_gateway_enabled" {
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
    should_enable_nat_gateway      = true
    should_enable_private_endpoint = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_subnet_nat_gateway_association.aks) == 1
    error_message = "AKS subnet NAT GW association should be created when NAT GW is enabled"
  }

  assert {
    condition     = azurerm_subnet.aks.default_outbound_access_enabled == false
    error_message = "AKS subnet should disable default outbound when NAT GW is enabled"
  }
}

run "nat_gateway_disabled" {
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
    should_enable_nat_gateway      = false
    should_enable_private_endpoint = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_subnet_nat_gateway_association.aks) == 0
    error_message = "AKS subnet NAT GW association should not be created when NAT GW is disabled"
  }

  assert {
    condition     = azurerm_subnet.aks.default_outbound_access_enabled == true
    error_message = "AKS subnet should enable default outbound when NAT GW is disabled"
  }
}

// ============================================================
// Cluster Admin Role Assignment Conditionals
// ============================================================

run "cluster_admin_enabled" {
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
    should_assign_cluster_admin    = true
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_cluster_admin) == 1
    error_message = "AKS cluster admin role should be assigned when enabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_rbac_cluster_admin) == 1
    error_message = "AKS RBAC cluster admin role should be assigned when enabled"
  }
}

run "cluster_admin_disabled" {
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
    should_assign_cluster_admin    = false
    should_enable_private_endpoint = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_cluster_admin) == 0
    error_message = "AKS cluster admin role should not be assigned when disabled"
  }

  assert {
    condition     = length(azurerm_role_assignment.aks_rbac_cluster_admin) == 0
    error_message = "AKS RBAC cluster admin role should not be assigned when disabled"
  }
}

// ============================================================
// Observability DCR Conditionals
// ============================================================

run "observability_with_dce" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    virtual_network                 = run.setup.virtual_network
    subnets                         = run.setup.subnets
    network_security_group          = run.setup.network_security_group
    nat_gateway                     = run.setup.nat_gateway
    log_analytics_workspace         = run.setup.log_analytics_workspace
    container_registry              = run.setup.container_registry
    data_collection_endpoint        = run.setup.data_collection_endpoint
    monitor_workspace               = run.setup.monitor_workspace
    should_deploy_dce               = true
    should_deploy_monitor_workspace = true
    should_enable_private_endpoint  = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.logs) == 1
    error_message = "Logs DCR should be created when DCE deployment is enabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.metrics) == 1
    error_message = "Metrics DCR should be created when DCE and monitor workspace deployments are enabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.logs) == 1
    error_message = "Logs DCRA should be created when DCE deployment is enabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.metrics) == 1
    error_message = "Metrics DCRA should be created when DCE and monitor workspace deployments are enabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.dce) == 1
    error_message = "DCE association should be created when DCE deployment is enabled"
  }
}

run "observability_without_dce" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    virtual_network                 = run.setup.virtual_network
    subnets                         = run.setup.subnets
    network_security_group          = run.setup.network_security_group
    nat_gateway                     = run.setup.nat_gateway
    log_analytics_workspace         = run.setup.log_analytics_workspace
    container_registry              = run.setup.container_registry
    data_collection_endpoint        = run.setup.data_collection_endpoint
    monitor_workspace               = run.setup.monitor_workspace
    should_deploy_dce               = false
    should_deploy_monitor_workspace = true
    should_enable_private_endpoint  = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.logs) == 0
    error_message = "Logs DCR should not be created when DCE deployment is disabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.metrics) == 0
    error_message = "Metrics DCR should not be created when DCE deployment is disabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.logs) == 0
    error_message = "Logs DCRA should not be created when DCE deployment is disabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.metrics) == 0
    error_message = "Metrics DCRA should not be created when DCE deployment is disabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.dce) == 0
    error_message = "DCE association should not be created when DCE deployment is disabled"
  }
}

// ============================================================
// Metrics DCR requires BOTH DCE and Monitor Workspace
// ============================================================

run "metrics_with_dce_without_monitor" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    virtual_network                 = run.setup.virtual_network
    subnets                         = run.setup.subnets
    network_security_group          = run.setup.network_security_group
    nat_gateway                     = run.setup.nat_gateway
    log_analytics_workspace         = run.setup.log_analytics_workspace
    container_registry              = run.setup.container_registry
    data_collection_endpoint        = run.setup.data_collection_endpoint
    monitor_workspace               = run.setup.monitor_workspace
    should_deploy_dce               = true
    should_deploy_monitor_workspace = false
    should_enable_private_endpoint  = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.logs) == 1
    error_message = "Logs DCR should still be created when DCE deployment is enabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.metrics) == 0
    error_message = "Metrics DCR should not be created when monitor workspace deployment is disabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule_association.metrics) == 0
    error_message = "Metrics DCRA should not be created when monitor workspace deployment is disabled"
  }
}

run "metrics_without_dce_with_monitor" {
  command = plan

  variables {
    resource_prefix                 = run.setup.resource_prefix
    environment                     = run.setup.environment
    instance                        = run.setup.instance
    location                        = run.setup.location
    resource_group                  = run.setup.resource_group
    virtual_network                 = run.setup.virtual_network
    subnets                         = run.setup.subnets
    network_security_group          = run.setup.network_security_group
    nat_gateway                     = run.setup.nat_gateway
    log_analytics_workspace         = run.setup.log_analytics_workspace
    container_registry              = run.setup.container_registry
    data_collection_endpoint        = run.setup.data_collection_endpoint
    monitor_workspace               = run.setup.monitor_workspace
    should_deploy_dce               = false
    should_deploy_monitor_workspace = true
    should_enable_private_endpoint  = false
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.logs) == 0
    error_message = "Logs DCR should not be created when DCE deployment is disabled"
  }

  assert {
    condition     = length(azurerm_monitor_data_collection_rule.metrics) == 0
    error_message = "Metrics DCR should not be created when DCE deployment is disabled even if monitor workspace deployment is enabled"
  }
}
