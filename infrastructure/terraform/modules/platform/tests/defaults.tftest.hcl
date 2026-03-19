// Platform module default configuration tests
// Validates behavior with only required variables (all defaults applied)

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
  current_user_oid = "00000000-0000-0000-0000-000000000001"
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "verify_defaults" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  // NAT Gateway enabled by default
  assert {
    condition     = length(azurerm_nat_gateway.main) == 1
    error_message = "NAT Gateway should be created by default"
  }

  // PE enabled by default
  assert {
    condition     = length(azurerm_subnet.private_endpoints) == 1
    error_message = "PE subnet should be created by default"
  }

  // Grafana enabled by default
  assert {
    condition     = length(azurerm_dashboard_grafana.main) == 1
    error_message = "Grafana should be created by default"
  }

  // Monitor Workspace enabled by default
  assert {
    condition     = length(azurerm_monitor_workspace.main) == 1
    error_message = "Monitor workspace should be created by default"
  }

  // AMPLS enabled by default (requires PE which is also default)
  assert {
    condition     = length(azurerm_monitor_private_link_scope.main) == 1
    error_message = "AMPLS should be created by default (PE is enabled)"
  }

  // DCE enabled by default
  assert {
    condition     = length(azurerm_monitor_data_collection_endpoint.main) == 1
    error_message = "DCE should be created by default"
  }

  // PostgreSQL NOT deployed by default
  assert {
    condition     = length(azurerm_postgresql_flexible_server.main) == 0
    error_message = "PostgreSQL should NOT be created by default"
  }

  // Redis NOT deployed by default
  assert {
    condition     = length(azurerm_managed_redis.main) == 0
    error_message = "Redis should NOT be created by default"
  }

  // AML Compute NOT deployed by default
  assert {
    condition     = length(azurerm_machine_learning_compute_cluster.gpu) == 0
    error_message = "AML compute cluster should NOT be created by default"
  }

  // OSMO identity enabled by default
  assert {
    condition     = length(azurerm_user_assigned_identity.osmo) == 1
    error_message = "OSMO identity should be created by default"
  }
}
