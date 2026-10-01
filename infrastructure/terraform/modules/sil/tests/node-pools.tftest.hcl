// SIL module node pool tests
// Validates subnet reuse, per-pool upgrade settings, scaling outputs, and node_pools validation

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
  aks_config = {
    system_node_pool_vm_size                    = "Standard_D8ds_v5"
    system_node_pool_node_count                 = 2
    should_enable_system_node_pool_auto_scaling = false
    should_enable_private_cluster               = false
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Existing Entry Shape (Regression)
// ============================================================

run "entry_without_new_attributes" {
  command = plan

  override_resource {
    target          = azurerm_subnet.gpu_node_pool["gpu"]
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001/subnets/snet-aks-gpu-test-dev-001"
    }
  }

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
    node_pools = {
      gpu = {
        vm_size                 = "Standard_NC40ads_H100_v5"
        node_count              = 1
        subnet_address_prefixes = ["10.0.20.0/24"]
      }
    }
  }

  assert {
    condition     = length(azurerm_subnet.gpu_node_pool) == 1 && contains(keys(azurerm_subnet.gpu_node_pool), "gpu")
    error_message = "An entry with subnet_address_prefixes should own its subnet"
  }

  assert {
    condition     = length(azurerm_subnet_network_security_group_association.gpu_node_pool) == 1 && length(azurerm_subnet_nat_gateway_association.gpu_node_pool) == 1
    error_message = "An owned subnet should keep its NSG and NAT gateway associations"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpu"].vnet_subnet_id == azurerm_subnet.gpu_node_pool["gpu"].id
    error_message = "An entry with subnet_address_prefixes should use its own subnet"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpu"].upgrade_settings[0].max_surge == "10%"
    error_message = "max_surge should default to 10% when neither max_surge nor max_unavailable is set"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpu"].upgrade_settings[0].max_unavailable == null
    error_message = "max_unavailable should stay unset by default"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpu"].upgrade_settings[0].drain_timeout_in_minutes == 0 && azurerm_kubernetes_cluster_node_pool.gpu["gpu"].upgrade_settings[0].node_soak_duration_in_minutes == 0
    error_message = "Drain timeout and node soak duration should stay 0"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["gpu"].upgrade_settings[0].undrainable_node_behavior == null
    error_message = "undrainable_node_behavior should stay unset by default"
  }
}

// ============================================================
// Subnet Reuse
// ============================================================

run "shared_subnet_uses_owner_subnet" {
  command = plan

  override_resource {
    target          = azurerm_subnet.gpu_node_pool["owner"]
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.Network/virtualNetworks/vnet-test-dev-001/subnets/snet-aks-owner-test-dev-001"
    }
  }

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
    node_pools = {
      owner = {
        vm_size                    = "Standard_NC40ads_H100_v5"
        subnet_address_prefixes    = ["10.0.20.0/24"]
        priority                   = "Spot"
        eviction_policy            = "Delete"
        should_enable_auto_scaling = true
        min_count                  = 0
        max_count                  = 1
      }
      sharer = {
        vm_size         = "Standard_NC40ads_H100_v5"
        node_count      = 1
        subnet_pool_key = "owner"
      }
    }
  }

  assert {
    condition     = length(azurerm_subnet.gpu_node_pool) == 1 && contains(keys(azurerm_subnet.gpu_node_pool), "owner")
    error_message = "Only the owner entry should create a subnet"
  }

  assert {
    condition     = length(azurerm_subnet_network_security_group_association.gpu_node_pool) == 1 && contains(keys(azurerm_subnet_network_security_group_association.gpu_node_pool), "owner")
    error_message = "Only the owner entry should create an NSG association"
  }

  assert {
    condition     = length(azurerm_subnet_nat_gateway_association.gpu_node_pool) == 1 && contains(keys(azurerm_subnet_nat_gateway_association.gpu_node_pool), "owner")
    error_message = "Only the owner entry should create a NAT gateway association"
  }

  assert {
    condition     = length(azurerm_kubernetes_cluster_node_pool.gpu) == 2
    error_message = "Both entries should create node pools"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["sharer"].vnet_subnet_id == azurerm_subnet.gpu_node_pool["owner"].id
    error_message = "A sharing entry should use its owner's subnet"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["owner"].vnet_subnet_id == azurerm_subnet.gpu_node_pool["owner"].id
    error_message = "The owner entry should keep its own subnet"
  }

  assert {
    condition     = length(output.gpu_node_pool_subnets) == 1 && contains(keys(output.gpu_node_pool_subnets), "owner")
    error_message = "gpu_node_pool_subnets should list only owned subnets"
  }
}

// ============================================================
// Upgrade Settings
// ============================================================

run "upgrade_settings_overrides" {
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
    node_pools = {
      unavailable = {
        vm_size                   = "Standard_NC40ads_H100_v5"
        node_count                = 1
        subnet_address_prefixes   = ["10.0.20.0/24"]
        max_unavailable           = "1"
        undrainable_node_behavior = "Schedule"
      }
      surge = {
        vm_size                   = "Standard_NC40ads_H100_v5"
        node_count                = 1
        subnet_address_prefixes   = ["10.0.21.0/24"]
        max_surge                 = "1"
        undrainable_node_behavior = "Cordon"
      }
      spot = {
        vm_size                    = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes    = ["10.0.22.0/24"]
        priority                   = "Spot"
        eviction_policy            = "Delete"
        should_enable_auto_scaling = true
        min_count                  = 1
        max_count                  = 1
      }
    }
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["unavailable"].upgrade_settings[0].max_unavailable == "1"
    error_message = "max_unavailable should map to upgrade_settings"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["unavailable"].upgrade_settings[0].max_surge == null
    error_message = "max_surge should be unset when max_unavailable is set"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["unavailable"].upgrade_settings[0].undrainable_node_behavior == "Schedule"
    error_message = "undrainable_node_behavior should map to upgrade_settings"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["surge"].upgrade_settings[0].max_surge == "1"
    error_message = "An explicit max_surge should replace the 10% default"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["surge"].upgrade_settings[0].max_unavailable == null
    error_message = "max_unavailable should stay unset when max_surge is set"
  }

  assert {
    condition     = azurerm_kubernetes_cluster_node_pool.gpu["surge"].upgrade_settings[0].undrainable_node_behavior == "Cordon"
    error_message = "undrainable_node_behavior Cordon should map to upgrade_settings"
  }

  assert {
    condition     = length(azurerm_kubernetes_cluster_node_pool.gpu["spot"].upgrade_settings) == 0
    error_message = "Spot pools should get no upgrade_settings"
  }
}

// ============================================================
// Scaling Fields in the node_pools Output
// ============================================================

run "node_pools_output_scaling_fields" {
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
    node_pools = {
      parked = {
        vm_size                    = "Standard_NC40ads_H100_v5"
        subnet_address_prefixes    = ["10.0.20.0/24"]
        should_enable_auto_scaling = false
        node_count                 = 0
      }
      autoscaled = {
        vm_size                    = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes    = ["10.0.21.0/24"]
        priority                   = "Spot"
        eviction_policy            = "Delete"
        should_enable_auto_scaling = true
        min_count                  = 1
        max_count                  = 1
      }
    }
  }

  assert {
    condition     = output.node_pools["parked"].should_enable_auto_scaling == false && output.node_pools["parked"].node_count == 0
    error_message = "node_pools output should report a parked pool's autoscaling and node count"
  }

  assert {
    condition     = output.node_pools["autoscaled"].should_enable_auto_scaling == true && output.node_pools["autoscaled"].min_count == 1 && output.node_pools["autoscaled"].max_count == 1
    error_message = "node_pools output should report an autoscaled pool's min and max counts"
  }

  assert {
    condition     = output.node_pools["autoscaled"].node_count == null
    error_message = "node_pools output should pass through an unset node_count"
  }
}

// ============================================================
// Validation
// ============================================================

run "rejects_missing_subnet_pool_key_target" {
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
    node_pools = {
      sharer = {
        vm_size         = "Standard_NC40ads_H100_v5"
        node_count      = 1
        subnet_pool_key = "absent"
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_chained_subnet_pool_key" {
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
    node_pools = {
      owner = {
        vm_size                 = "Standard_NC40ads_H100_v5"
        node_count              = 1
        subnet_address_prefixes = ["10.0.20.0/24"]
      }
      first = {
        vm_size         = "Standard_NC40ads_H100_v5"
        node_count      = 1
        subnet_pool_key = "owner"
      }
      second = {
        vm_size         = "Standard_NC40ads_H100_v5"
        node_count      = 1
        subnet_pool_key = "first"
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_entry_without_subnet" {
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
    node_pools = {
      orphan = {
        vm_size    = "Standard_NC40ads_H100_v5"
        node_count = 1
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_subnet_prefixes_with_subnet_pool_key" {
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
    node_pools = {
      owner = {
        vm_size                 = "Standard_NC40ads_H100_v5"
        node_count              = 1
        subnet_address_prefixes = ["10.0.20.0/24"]
      }
      both = {
        vm_size                 = "Standard_NC40ads_H100_v5"
        node_count              = 1
        subnet_address_prefixes = ["10.0.21.0/24"]
        subnet_pool_key         = "owner"
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_max_surge_with_max_unavailable" {
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
    node_pools = {
      both = {
        vm_size                 = "Standard_NC40ads_H100_v5"
        node_count              = 1
        subnet_address_prefixes = ["10.0.20.0/24"]
        max_surge               = "1"
        max_unavailable         = "1"
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_upgrade_settings_on_spot" {
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
    node_pools = {
      spot = {
        vm_size                   = "Standard_NV36ads_A10_v5"
        subnet_address_prefixes   = ["10.0.20.0/24"]
        priority                  = "Spot"
        eviction_policy           = "Delete"
        undrainable_node_behavior = "Schedule"
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_invalid_undrainable_node_behavior" {
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
    node_pools = {
      regular = {
        vm_size                   = "Standard_NC40ads_H100_v5"
        node_count                = 1
        subnet_address_prefixes   = ["10.0.20.0/24"]
        undrainable_node_behavior = "Drain"
      }
    }
  }

  expect_failures = [var.node_pools]
}

run "rejects_reserved_accelerator_label" {
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
    node_pools = {
      gpu = {
        vm_size                 = "Standard_NV36ads_A10_v5"
        node_count              = 1
        subnet_address_prefixes = ["10.0.20.0/24"]
        node_labels             = { accelerator = "nvidia" }
      }
    }
  }

  expect_failures = [var.node_pools]
}
