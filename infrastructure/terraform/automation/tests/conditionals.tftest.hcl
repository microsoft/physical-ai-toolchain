// Automation standalone conditional data source tests
// Validates should_start_postgresql controls PostgreSQL data source lookup

mock_provider "azurerm" {}

override_data {
  target = data.azurerm_resource_group.this
  values = {
    id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001"
    name     = "rg-test-dev-001"
    location = "westus3"
  }
}

override_data {
  target = data.azurerm_kubernetes_cluster.this
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.ContainerService/managedClusters/aks-test-dev-001"
    name = "aks-test-dev-001"
  }
}

override_data {
  target = data.azurerm_postgresql_flexible_server.this[0]
  values = {
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-test-dev-001"
    name = "psql-test-dev-001"
  }
}

// ============================================================
// PostgreSQL Startup Enabled
// ============================================================

run "postgresql_startup_enabled" {
  command = plan

  variables {
    resource_prefix         = "test"
    environment             = "dev"
    instance                = "001"
    location                = "westus3"
    should_start_postgresql = true
  }

  assert {
    condition     = length(data.azurerm_postgresql_flexible_server.this) == 1
    error_message = "PostgreSQL data source should exist when should_start_postgresql is true"
  }
}

// ============================================================
// PostgreSQL Startup Disabled
// ============================================================

run "postgresql_startup_disabled" {
  command = plan

  variables {
    resource_prefix         = "test"
    environment             = "dev"
    instance                = "001"
    location                = "westus3"
    should_start_postgresql = false
  }

  assert {
    condition     = length(data.azurerm_postgresql_flexible_server.this) == 0
    error_message = "PostgreSQL data source should not exist when should_start_postgresql is false"
  }
}
