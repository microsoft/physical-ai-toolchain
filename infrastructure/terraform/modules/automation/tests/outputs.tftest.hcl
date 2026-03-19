// Automation module output structure tests
// Validates output contracts for input-derived attributes

mock_provider "azurerm" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

run "verify_outputs" {
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

  assert {
    condition     = output.automation_account.name == "aa-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "automation_account.name should match naming convention"
  }

  assert {
    condition     = output.runbook.name == "Start-AzureResources"
    error_message = "runbook.name should be Start-AzureResources"
  }

  assert {
    condition     = output.schedule.name == "morning-startup"
    error_message = "schedule.name should be morning-startup"
  }

  assert {
    condition     = output.schedule.timezone == "UTC"
    error_message = "schedule.timezone should default to UTC"
  }
}
