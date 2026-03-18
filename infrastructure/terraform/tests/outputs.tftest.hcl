// Root output structure tests
// Validates output presence and nullability when features are disabled

mock_provider "azurerm" {}
mock_provider "azuread" {}
mock_provider "azapi" {}
mock_provider "msgraph" {}
mock_provider "tls" {}
mock_provider "random" {}

override_data {
  target = module.platform.data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Core Outputs Present
// ============================================================

run "core_outputs_present" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
  }

  assert {
    condition     = output.resource_group != null
    error_message = "resource_group output should not be null"
  }

  assert {
    condition     = output.key_vault != null
    error_message = "key_vault output should not be null"
  }

  assert {
    condition     = output.aks_cluster != null
    error_message = "aks_cluster output should not be null"
  }
}

// ============================================================
// Optional Outputs Null When Disabled
// ============================================================

run "optional_outputs_null_when_disabled" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
    should_deploy_postgresql     = false
    should_deploy_redis          = false
    should_deploy_grafana        = false
    should_deploy_aml_compute    = false
  }

  assert {
    condition     = output.postgresql == null
    error_message = "postgresql output should be null when PostgreSQL is disabled"
  }

  assert {
    condition     = output.redis == null
    error_message = "redis output should be null when Redis is disabled"
  }

  assert {
    condition     = output.grafana == null
    error_message = "grafana output should be null when Grafana is disabled"
  }

  assert {
    condition     = output.aml_compute_cluster == null
    error_message = "aml_compute_cluster output should be null when AML compute is disabled"
  }
}
