// Automation module schedule tests
// Validates schedule frequency, week days, and timezone configuration

mock_provider "azurerm" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Default Schedule Configuration
// ============================================================

run "default_schedule" {
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
    condition     = azurerm_automation_schedule.morning_startup.frequency == "Week"
    error_message = "Schedule frequency should be Week"
  }

  assert {
    condition     = azurerm_automation_schedule.morning_startup.timezone == "UTC"
    error_message = "Default timezone should be UTC"
  }

  assert {
    condition     = length(azurerm_automation_schedule.morning_startup.week_days) == 5
    error_message = "Default schedule should run 5 days per week (Mon-Fri)"
  }
}

// ============================================================
// Custom Schedule Configuration
// ============================================================

run "custom_schedule" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    aks_cluster         = run.setup.aks_cluster
    runbook_script_path = run.setup.runbook_script_path
    schedule_config = {
      start_time = "09:30"
      week_days  = ["Monday", "Wednesday", "Friday"]
      timezone   = "America/Los_Angeles"
    }
  }

  assert {
    condition     = azurerm_automation_schedule.morning_startup.timezone == "America/Los_Angeles"
    error_message = "Custom timezone should be applied"
  }

  assert {
    condition     = length(azurerm_automation_schedule.morning_startup.week_days) == 3
    error_message = "Custom schedule should run 3 days per week"
  }
}
