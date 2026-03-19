// Automation module conditional resource tests
// Validates PostgreSQL role assignment conditionals

mock_provider "azurerm" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// PostgreSQL Conditionals
// ============================================================

run "postgresql_provided" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    aks_cluster         = run.setup.aks_cluster
    runbook_script_path = run.setup.runbook_script_path
    postgresql_server = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test-dev-001/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-test-dev-001"
      name = "psql-test-dev-001"
    }
  }

  assert {
    condition     = length(azurerm_role_assignment.postgresql_contributor) == 1
    error_message = "PostgreSQL role assignment should exist when server is provided"
  }

  assert {
    condition     = azurerm_automation_job_schedule.start_resources.parameters["postgresservername"] == "psql-test-dev-001"
    error_message = "Job schedule should include PostgreSQL server name when provided"
  }
}

run "postgresql_null" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    aks_cluster         = run.setup.aks_cluster
    runbook_script_path = run.setup.runbook_script_path
    postgresql_server   = null
  }

  assert {
    condition     = length(azurerm_role_assignment.postgresql_contributor) == 0
    error_message = "PostgreSQL role assignment should not exist when server is null"
  }

  assert {
    condition     = azurerm_automation_job_schedule.start_resources.parameters["postgresservername"] == ""
    error_message = "Job schedule should have empty PostgreSQL server name when null"
  }
}
