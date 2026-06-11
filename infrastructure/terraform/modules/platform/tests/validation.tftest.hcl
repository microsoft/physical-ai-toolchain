// Platform module variable validation tests
// Validates that invalid variable values are rejected by validation blocks

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "random" {}

override_data {
  target = data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

variables {
  current_user_oid                   = "00000000-0000-0000-0000-000000000001"
  aml_managed_network_isolation_mode = "Disabled"
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// NAT Gateway Zones — Invalid Zone Value
// ============================================================

run "nat_gateway_zones_invalid_zone_rejected" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["4"]
  }

  expect_failures = [var.nat_gateway_zones]
}

// ============================================================
// NAT Gateway Zones — Non-numeric Value
// ============================================================

run "nat_gateway_zones_non_numeric_rejected" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["abc"]
  }

  expect_failures = [var.nat_gateway_zones]
}

// ============================================================
// NAT Gateway Zones — Duplicate Zones
// ============================================================

run "nat_gateway_zones_duplicates_rejected" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["1", "1"]
  }

  expect_failures = [var.nat_gateway_zones]
}

// ============================================================
// NAT Gateway Zones — Valid Single Zone
// ============================================================

run "nat_gateway_zones_single_zone_accepted" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["2"]
  }
}

// ============================================================
// NAT Gateway Zones — Valid Multiple Zones
// ============================================================

run "nat_gateway_zones_multiple_zones_accepted" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = ["1", "2", "3"]
  }
}

// ============================================================
// NAT Gateway Zones — Empty List (No AZ Support)
// ============================================================

run "nat_gateway_zones_empty_accepted" {
  command = plan

  variables {
    resource_prefix   = run.setup.resource_prefix
    environment       = run.setup.environment
    instance          = run.setup.instance
    location          = run.setup.location
    resource_group    = run.setup.resource_group
    current_user_oid  = run.setup.current_user_oid
    nat_gateway_zones = []
  }
}

// ============================================================
// AML Managed Network Isolation Mode Validation
// ============================================================

run "aml_isolation_mode_invalid_rejected" {
  command = plan

  variables {
    resource_prefix                    = run.setup.resource_prefix
    environment                        = run.setup.environment
    instance                           = run.setup.instance
    location                           = run.setup.location
    resource_group                     = run.setup.resource_group
    current_user_oid                   = run.setup.current_user_oid
    aml_managed_network_isolation_mode = "Blocked"
  }

  expect_failures = [var.aml_managed_network_isolation_mode]
}

run "aml_isolation_mode_allow_internet_outbound_accepted" {
  command = plan

  variables {
    resource_prefix                    = run.setup.resource_prefix
    environment                        = run.setup.environment
    instance                           = run.setup.instance
    location                           = run.setup.location
    resource_group                     = run.setup.resource_group
    current_user_oid                   = run.setup.current_user_oid
    aml_managed_network_isolation_mode = "AllowInternetOutbound"
  }
}

run "aml_isolation_mode_allow_only_approved_outbound_accepted" {
  command = plan

  variables {
    resource_prefix                    = run.setup.resource_prefix
    environment                        = run.setup.environment
    instance                           = run.setup.instance
    location                           = run.setup.location
    resource_group                     = run.setup.resource_group
    current_user_oid                   = run.setup.current_user_oid
    aml_managed_network_isolation_mode = "AllowOnlyApprovedOutbound"
  }
}

// ============================================================
// AML Compute Cluster Map Validation
// ============================================================

run "aml_compute_subnet_with_managed_network_rejected" {
  command = plan

  variables {
    resource_prefix                    = run.setup.resource_prefix
    environment                        = run.setup.environment
    instance                           = run.setup.instance
    location                           = run.setup.location
    resource_group                     = run.setup.resource_group
    current_user_oid                   = run.setup.current_user_oid
    aml_managed_network_isolation_mode = "AllowOnlyApprovedOutbound"
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
        subnet_id             = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-aml"
      }
    }
  }

  expect_failures = [azurerm_machine_learning_compute_cluster.gpu["gpu-cluster"]]
}

run "aml_compute_invalid_name_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu_cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_invalid_vm_priority_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "Spot"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_negative_min_node_count_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = -1
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_negative_max_node_count_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = -1
        scale_down_after_idle = "PT5M"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_min_node_count_greater_than_max_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 2
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_invalid_identity_type_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
        identity_type         = "SystemAssigned, UserAssigned"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_invalid_scale_down_after_idle_rejected" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "5 minutes"
      }
    }
  }

  expect_failures = [var.aml_compute_clusters]
}

run "aml_compute_compound_scale_down_after_idle_accepted" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "PT1H30M"
      }
    }
  }
}
