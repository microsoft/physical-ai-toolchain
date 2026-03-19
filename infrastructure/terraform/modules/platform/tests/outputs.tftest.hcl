// Platform module output structure tests
// Validates output contracts (presence and nullability)
// Uses command = plan for count-based assertions; some outputs with computed values
// cannot be fully validated with mock providers (DR-07)

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

run "null_outputs_when_disabled" {
  command = plan

  variables {
    resource_prefix             = run.setup.resource_prefix
    environment                 = run.setup.environment
    instance                    = run.setup.instance
    location                    = run.setup.location
    resource_group              = run.setup.resource_group
    current_user_oid            = run.setup.current_user_oid
    should_enable_nat_gateway   = false
    should_deploy_grafana       = false
    should_deploy_aml_compute   = false
    should_enable_osmo_identity = false
  }

  assert {
    condition     = output.nat_gateway == null
    error_message = "nat_gateway output should be null when NAT Gateway is disabled"
  }

  assert {
    condition     = output.grafana == null
    error_message = "grafana output should be null when Grafana is disabled"
  }

  assert {
    condition     = output.aml_compute_cluster == null
    error_message = "aml_compute_cluster output should be null when AML compute is disabled"
  }

  assert {
    condition     = output.osmo_workload_identity == null
    error_message = "osmo_workload_identity output should be null when OSMO identity is disabled"
  }
}

run "private_dns_zones_empty_when_pe_disabled" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = false
  }

  assert {
    condition     = length(output.private_dns_zones) == 0
    error_message = "private_dns_zones output should be empty when PE is disabled"
  }
}

run "postgresql_null_when_disabled" {
  command = plan

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    resource_group           = run.setup.resource_group
    current_user_oid         = run.setup.current_user_oid
    should_deploy_postgresql = false
  }

  assert {
    condition     = output.postgresql == null
    error_message = "postgresql output should be null when PostgreSQL is disabled"
  }
}

run "redis_null_when_disabled" {
  command = plan

  variables {
    resource_prefix     = run.setup.resource_prefix
    environment         = run.setup.environment
    instance            = run.setup.instance
    location            = run.setup.location
    resource_group      = run.setup.resource_group
    current_user_oid    = run.setup.current_user_oid
    should_deploy_redis = false
  }

  assert {
    condition     = output.redis == null
    error_message = "redis output should be null when Redis is disabled"
  }
}
