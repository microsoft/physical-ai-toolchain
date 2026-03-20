// Automation standalone data source naming tests
// Validates default name derivation and explicit name overrides for data source lookups

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
// Default Name Derivation
// ============================================================

run "default_name_derivation" {
  command = plan

  variables {
    resource_prefix         = "test"
    environment             = "dev"
    instance                = "001"
    location                = "westus3"
    should_start_postgresql = true
  }

  assert {
    condition     = data.azurerm_resource_group.this.name == "rg-test-dev-001"
    error_message = "Resource group should use default name rg-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = data.azurerm_kubernetes_cluster.this.name == "aks-test-dev-001"
    error_message = "AKS cluster should use default name aks-{prefix}-{env}-{instance}"
  }

  assert {
    condition     = data.azurerm_postgresql_flexible_server.this[0].name == "psql-test-dev-001"
    error_message = "PostgreSQL should use default name psql-{prefix}-{env}-{instance}"
  }
}

// ============================================================
// Explicit Name Override
// ============================================================

run "name_override" {
  command = plan

  override_data {
    target = data.azurerm_resource_group.this
    values = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/custom-rg"
      name     = "custom-rg"
      location = "eastus2"
    }
  }

  override_data {
    target = data.azurerm_kubernetes_cluster.this
    values = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/custom-rg/providers/Microsoft.ContainerService/managedClusters/custom-aks"
      name = "custom-aks"
    }
  }

  override_data {
    target = data.azurerm_postgresql_flexible_server.this[0]
    values = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/custom-rg/providers/Microsoft.DBforPostgreSQL/flexibleServers/custom-psql"
      name = "custom-psql"
    }
  }

  variables {
    resource_prefix         = "test"
    environment             = "dev"
    instance                = "001"
    location                = "eastus2"
    resource_group_name     = "custom-rg"
    aks_cluster_name        = "custom-aks"
    postgresql_name         = "custom-psql"
    should_start_postgresql = true
  }

  assert {
    condition     = data.azurerm_resource_group.this.name == "custom-rg"
    error_message = "Resource group should use explicit override name"
  }

  assert {
    condition     = data.azurerm_kubernetes_cluster.this.name == "custom-aks"
    error_message = "AKS cluster should use explicit override name"
  }

  assert {
    condition     = data.azurerm_postgresql_flexible_server.this[0].name == "custom-psql"
    error_message = "PostgreSQL should use explicit override name"
  }
}
