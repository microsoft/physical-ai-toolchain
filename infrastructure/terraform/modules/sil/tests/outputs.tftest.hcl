// SIL module output structure tests
// Validates output contracts and passthrough behavior

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "tls" {}
mock_provider "random" {}

variables {
  should_assign_cluster_admin    = false
  should_enable_private_endpoint = false
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// AKS Cluster Output Structure
// ============================================================

run "aks_cluster_output_keys" {
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
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  // Verify output contains the expected name
  assert {
    condition     = output.aks_cluster.name == "aks-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "aks_cluster output name should follow naming convention"
  }
}

// ============================================================
// AKS Subnets Output Structure
// ============================================================

run "aks_subnets_structure" {
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
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = output.aks_subnets.aks.name == "snet-aks-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "aks_subnets output should have 'aks' key with correct name"
  }
}

// ============================================================
// GPU Subnets Match Pools
// ============================================================

run "gpu_subnets_match_pools" {
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
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
    node_pools = {
      pool1 = {
        vm_size                 = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes = ["10.0.16.0/24"]
        priority                = "Spot"
        eviction_policy         = "Delete"
      }
      pool2 = {
        vm_size                 = "Standard_NC24ads_A100_v4"
        subnet_address_prefixes = ["10.0.17.0/24"]
        priority                = "Regular"
      }
    }
  }

  assert {
    condition     = length(output.gpu_node_pool_subnets) == 2
    error_message = "gpu_node_pool_subnets should have keys matching input pool names"
  }

  assert {
    condition     = output.gpu_node_pool_subnets["pool1"].name == "snet-aks-pool1-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "GPU subnet name should follow snet-aks-{pool_name}-{prefix}-{env}-{instance}"
  }
}

// ============================================================
// Node Pools Passthrough
// ============================================================

run "node_pools_passthrough" {
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
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
    node_pools = {
      testpool = {
        vm_size                 = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes = ["10.0.16.0/24"]
        node_taints             = ["nvidia.com/gpu:NoSchedule"]
        priority                = "Spot"
        eviction_policy         = "Delete"
        node_labels             = { "workload" = "gpu" }
      }
    }
  }

  assert {
    condition     = output.node_pools["testpool"].vm_size == "Standard_NV36ads_A10_v5"
    error_message = "node_pools output should pass through vm_size from input"
  }

  assert {
    condition     = output.node_pools["testpool"].priority == "Spot"
    error_message = "node_pools output should pass through priority from input"
  }

  assert {
    condition     = contains(output.node_pools["testpool"].node_taints, "nvidia.com/gpu:NoSchedule")
    error_message = "node_pools output should pass through node_taints from input"
  }

  assert {
    condition     = output.node_pools["testpool"].node_labels["workload"] == "gpu"
    error_message = "node_pools output should pass through node_labels from input"
  }
}

// ============================================================
// Empty Node Pools Output
// ============================================================

run "empty_pools_output" {
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
    aks_config = {
      system_node_pool_vm_size                    = "Standard_D8ds_v5"
      system_node_pool_node_count                 = 2
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
    node_pools = {}
  }

  assert {
    condition     = length(output.gpu_node_pool_subnets) == 0
    error_message = "gpu_node_pool_subnets should be empty when no node pools"
  }

  assert {
    condition     = length(output.node_pools) == 0
    error_message = "node_pools output should be empty when no input pools"
  }
}
