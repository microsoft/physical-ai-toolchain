// SIL module AKS configuration tests
// Validates AKS cluster, system pool, GPU node pool, and network profile configuration

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
// System Node Pool Autoscaling
// ============================================================

run "system_pool_autoscaling_on" {
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
      should_enable_system_node_pool_auto_scaling = true
      system_node_pool_min_count                  = 1
      system_node_pool_max_count                  = 5
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.default_node_pool[0].auto_scaling_enabled == true
    error_message = "System pool autoscaling should be enabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.default_node_pool[0].min_count == 1
    error_message = "System pool min_count should be set when autoscaling is enabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.default_node_pool[0].max_count == 5
    error_message = "System pool max_count should be set when autoscaling is enabled"
  }
}

run "system_pool_autoscaling_off" {
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
      system_node_pool_node_count                 = 3
      should_enable_system_node_pool_auto_scaling = false
      should_enable_private_cluster               = false
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.default_node_pool[0].auto_scaling_enabled == false
    error_message = "System pool autoscaling should be disabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.default_node_pool[0].node_count == 3
    error_message = "System pool node_count should match configured value when autoscaling is off"
  }
}

// ============================================================
// GPU Node Pool Types
// ============================================================

run "spot_node_pool" {
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
      gpuspot = {
        vm_size                 = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes = ["10.0.16.0/24"]
        priority                = "Spot"
        eviction_policy         = "Delete"
      }
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpuspot"].priority == "Spot"
    error_message = "Spot pool priority should be Spot"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpuspot"].eviction_policy == "Delete"
    error_message = "Spot pool eviction_policy should be set"
  }
}

run "regular_node_pool" {
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
      gpureg = {
        vm_size                 = "Standard_NC24ads_A100_v4"
        node_count              = 2
        subnet_address_prefixes = ["10.0.17.0/24"]
        priority                = "Regular"
      }
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpureg"].priority == "Regular"
    error_message = "Regular pool priority should be Regular"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpureg"].eviction_policy == null
    error_message = "Regular pool eviction_policy should be null"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpureg"].node_count == 2
    error_message = "Regular pool node_count should match input"
  }
}

// ============================================================
// Static Cluster Configuration
// ============================================================

run "static_cluster_config" {
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
    condition     = azurerm_kubernetes_cluster.main.sku_tier == "Standard"
    error_message = "AKS SKU tier should be Standard"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.local_account_disabled == true
    error_message = "Local accounts should be disabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.oidc_issuer_enabled == true
    error_message = "OIDC issuer should be enabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.workload_identity_enabled == true
    error_message = "Workload identity should be enabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.azure_policy_enabled == true
    error_message = "Azure Policy should be enabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.role_based_access_control_enabled == true
    error_message = "RBAC should be enabled"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.node_provisioning_profile[0].mode == "Manual"
    error_message = "AKS node provisioning mode should remain manual"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.node_provisioning_profile[0].default_node_pools == "Auto"
    error_message = "AKS default node pools should remain provider-managed"
  }
}

// ============================================================
// Network Profile
// ============================================================

run "network_profile" {
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
    condition     = azurerm_kubernetes_cluster.main.network_profile[0].network_plugin == "azure"
    error_message = "Network plugin should be azure"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.network_profile[0].network_plugin_mode == "overlay"
    error_message = "Network plugin mode should be overlay"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.network_profile[0].network_policy == "azure"
    error_message = "Network policy should be azure"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.network_profile[0].service_cidr == "172.16.0.0/16"
    error_message = "Service CIDR should be 172.16.0.0/16"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.network_profile[0].dns_service_ip == "172.16.0.10"
    error_message = "DNS service IP should be 172.16.0.10"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.network_profile[0].pod_cidr == "10.244.0.0/16"
    error_message = "Pod CIDR should be 10.244.0.0/16"
  }
}

// ============================================================
// Multiple and Empty Node Pools
// ============================================================

run "multiple_node_pools" {
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
      gpu1 = {
        vm_size                 = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes = ["10.0.16.0/24"]
        priority                = "Spot"
        eviction_policy         = "Delete"
      }
      gpu2 = {
        vm_size                 = "Standard_NC24ads_A100_v4"
        subnet_address_prefixes = ["10.0.17.0/24"]
        priority                = "Regular"
      }
    }
  }

  assert {
    condition     = length(azurerm_kubernetes_cluster_node_pool.gpu) == 2
    error_message = "Should create 2 GPU node pools"
  }

  assert {
    condition     = length(azurerm_subnet.gpu_node_pool) == 2
    error_message = "Should create 2 GPU subnets matching pool count"
  }
}

run "empty_node_pools" {
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
    condition     = length(azurerm_kubernetes_cluster_node_pool.gpu) == 0
    error_message = "Should create 0 GPU node pools when empty"
  }

  assert {
    condition     = length(azurerm_subnet.gpu_node_pool) == 0
    error_message = "Should create 0 GPU subnets when no node pools"
  }
}

// ============================================================
// Cluster SKU Tier and Support Plan
// ============================================================

run "cluster_tier_defaults" {
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
    condition     = azurerm_kubernetes_cluster.main.sku_tier == "Standard" && azurerm_kubernetes_cluster.main.support_plan == "KubernetesOfficial"
    error_message = "Clusters should default to the Standard tier and KubernetesOfficial support plan"
  }
}

run "cluster_tier_premium_long_term_support" {
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
      sku_tier                                    = "Premium"
      support_plan                                = "AKSLongTermSupport"
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.sku_tier == "Premium" && azurerm_kubernetes_cluster.main.support_plan == "AKSLongTermSupport"
    error_message = "sku_tier and support_plan should pass through to the cluster"
  }
}

run "rejects_long_term_support_without_premium" {
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
      support_plan                                = "AKSLongTermSupport"
    }
  }

  expect_failures = [var.aks_config]
}

run "rejects_invalid_sku_tier" {
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
      sku_tier                                    = "Basic"
    }
  }

  expect_failures = [var.aks_config]
}

// ============================================================
// Cluster Autoscaler Profile
// ============================================================

run "auto_scaler_profile_unset" {
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
    condition     = length(azurerm_kubernetes_cluster.main.auto_scaler_profile) == 0
    error_message = "An unset auto_scaler_profile should render no auto_scaler_profile block"
  }
}

run "auto_scaler_profile_set" {
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
      auto_scaler_profile = {
        expander               = "least-waste"
        new_pod_scale_up_delay = "120s"
      }
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.auto_scaler_profile[0].expander == "least-waste"
    error_message = "expander should pass through to the cluster autoscaler profile"
  }

  assert {
    condition     = azurerm_kubernetes_cluster.main.auto_scaler_profile[0].new_pod_scale_up_delay == "120s"
    error_message = "new_pod_scale_up_delay should pass through to the cluster autoscaler profile"
  }
}

run "rejects_invalid_auto_scaler_expander" {
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
      auto_scaler_profile = {
        expander = "price"
      }
    }
  }

  expect_failures = [var.aks_config]
}

run "rejects_invalid_new_pod_scale_up_delay" {
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
      auto_scaler_profile = {
        new_pod_scale_up_delay = "2 minutes"
      }
    }
  }

  expect_failures = [var.aks_config]
}

run "rejects_minutes_new_pod_scale_up_delay" {
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
      auto_scaler_profile = {
        new_pod_scale_up_delay = "2m"
      }
    }
  }

  expect_failures = [var.aks_config]
}
