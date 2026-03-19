// Automation module naming convention tests
// Validates resource names follow expected conventions

mock_provider "azurerm" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "verify_resource_naming" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    aks_cluster         = run.setup.aks_cluster
    runbook_script_path = run.setup.runbook_script_path
  }

  // Automation Account
  assert {
    condition     = azurerm_automation_account.this.name == "aa-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Automation account name must follow aa-{prefix}-{env}-{instance}"
  }

  // Runbook (fixed name)
  assert {
    condition     = azurerm_automation_runbook.start_resources.name == "Start-AzureResources"
    error_message = "Runbook name must be Start-AzureResources"
  }

  // Schedule (fixed name)
  assert {
    condition     = azurerm_automation_schedule.morning_startup.name == "morning-startup"
    error_message = "Schedule name must be morning-startup"
  }
}
