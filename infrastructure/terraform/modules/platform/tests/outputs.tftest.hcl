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
  current_user_oid                   = "00000000-0000-0000-0000-000000000001"
  aml_managed_network_isolation_mode = "Disabled"
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
    condition     = length(output.aml_compute_clusters) == 0
    error_message = "aml_compute_clusters output should be empty when no AML compute clusters are configured"
  }

  assert {
    condition     = output.osmo_workload_identity == null
    error_message = "osmo_workload_identity output should be null when OSMO identity is disabled"
  }
}

run "aml_compute_clusters_output_populated" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
    aml_compute_clusters = {
      gpu-training = {
        vm_size               = "Standard_NC4as_T4_v3"
        vm_priority           = "LowPriority"
        min_node_count        = 0
        max_node_count        = 1
        scale_down_after_idle = "PT5M"
      }
      gpu-eval = {
        vm_size               = "Standard_NC8as_T4_v3"
        vm_priority           = "Dedicated"
        min_node_count        = 1
        max_node_count        = 2
        scale_down_after_idle = "PT10M"
      }
    }
  }

  assert {
    condition     = length(output.aml_compute_clusters) == 2
    error_message = "aml_compute_clusters output should include each configured cluster"
  }

  assert {
    condition     = output.aml_compute_clusters["gpu-training"].name == "gpu-training"
    error_message = "aml_compute_clusters output should be keyed by cluster name"
  }

  assert {
    condition     = length(keys(output.aml_compute_clusters["gpu-training"])) == 2 && contains(keys(output.aml_compute_clusters["gpu-training"]), "id") && contains(keys(output.aml_compute_clusters["gpu-training"]), "name")
    error_message = "aml_compute_clusters output values should expose only id and name"
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
