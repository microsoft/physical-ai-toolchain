// SIL module OSMO federated identity credential tests
// Validates FIC creation gated by identity presence and federation flag

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "tls" {}
mock_provider "random" {}

variables {
  should_assign_cluster_admin     = false
  should_enable_private_endpoint  = false
  should_deploy_dce               = false
  should_deploy_monitor_workspace = false
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// OSMO Federation Enabled with Identity
// ============================================================

run "federation_enabled_with_identity" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    network_security_group  = run.setup.network_security_group
    nat_gateway             = run.setup.nat_gateway
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    osmo_workload_identity  = run.setup.osmo_workload_identity
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
    osmo_config = {
      should_federate_identity = true
      control_plane_namespace  = "osmo-control-plane"
      operator_namespace       = "osmo-operator"
      workflows_namespace      = "osmo-workflows"
    }
  }

  // 5 named ServiceAccount FICs
  assert {
    condition     = length(azurerm_federated_identity_credential.osmo) == 5
    error_message = "Should create 5 OSMO named FICs when federation and identity are both enabled"
  }

  // 3 default ServiceAccount FICs (one per namespace)
  assert {
    condition     = length(azurerm_federated_identity_credential.osmo_default_sa) == 3
    error_message = "Should create 3 OSMO default SA FICs (one per namespace)"
  }

  // Verify audience is set correctly
  assert {
    condition     = azurerm_federated_identity_credential.osmo["osmo-control-plane"].audience[0] == "api://AzureADTokenExchange"
    error_message = "OSMO FIC audience should be api://AzureADTokenExchange"
  }
}

// ============================================================
// OSMO Federation Disabled
// ============================================================

run "federation_disabled" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    network_security_group  = run.setup.network_security_group
    nat_gateway             = run.setup.nat_gateway
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    osmo_workload_identity  = run.setup.osmo_workload_identity
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
    osmo_config = {
      should_federate_identity = false
      control_plane_namespace  = "osmo-control-plane"
      operator_namespace       = "osmo-operator"
      workflows_namespace      = "osmo-workflows"
    }
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.osmo) == 0
    error_message = "Should create 0 named FICs when federation is disabled"
  }

  assert {
    condition     = length(azurerm_federated_identity_credential.osmo_default_sa) == 0
    error_message = "Should create 0 default SA FICs when federation is disabled"
  }
}

// ============================================================
// DR-02: osmo_default_sa only checks should_federate_identity but NOT
// osmo_workload_identity != null. Setting should_federate_identity = true
// without providing osmo_workload_identity causes a null dereference on
// var.osmo_workload_identity.id (plan-time error, not testable via expect_failures).
// The named FICs (osmo) correctly have the dual guard.

// ============================================================
// Credential Subject Format
// ============================================================

run "credential_subject_format" {
  command = plan

  variables {
    resource_prefix         = run.setup.resource_prefix
    environment             = run.setup.environment
    instance                = run.setup.instance
    location                = run.setup.location
    resource_group          = run.setup.resource_group
    virtual_network         = run.setup.virtual_network
    subnets                 = run.setup.subnets
    network_security_group  = run.setup.network_security_group
    nat_gateway             = run.setup.nat_gateway
    log_analytics_workspace = run.setup.log_analytics_workspace
    container_registry      = run.setup.container_registry
    osmo_workload_identity  = run.setup.osmo_workload_identity
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
    osmo_config = {
      should_federate_identity = true
      control_plane_namespace  = "osmo-control-plane"
      operator_namespace       = "osmo-operator"
      workflows_namespace      = "osmo-workflows"
    }
  }

  // Control plane SA subject format
  assert {
    condition     = azurerm_federated_identity_credential.osmo["osmo-control-plane"].subject == "system:serviceaccount:osmo-control-plane:osmo-control-plane"
    error_message = "Control plane FIC subject must match system:serviceaccount:{ns}:{sa}"
  }

  // Router SA subject format
  assert {
    condition     = azurerm_federated_identity_credential.osmo["osmo-router"].subject == "system:serviceaccount:osmo-control-plane:router"
    error_message = "Router FIC subject must match system:serviceaccount:osmo-control-plane:router"
  }

  // Default SA subject format
  assert {
    condition     = azurerm_federated_identity_credential.osmo_default_sa["osmo-control-plane"].subject == "system:serviceaccount:osmo-control-plane:default"
    error_message = "Default SA FIC subject must match system:serviceaccount:{ns}:default"
  }
}
