// sigstore-mirror module conditional resource tests
// Validates should_deploy gating and storage_replication_type wiring

mock_provider "azurerm" {}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// should_deploy Gating
// ============================================================

run "should_deploy_false_creates_no_resources" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    should_deploy   = false
  }

  assert {
    condition     = length(azurerm_storage_account.mirror) == 0
    error_message = "Storage account must not be created when should_deploy is false"
  }

  assert {
    condition     = length(azurerm_storage_container.web) == 0
    error_message = "Web container must not be created when should_deploy is false"
  }
}

run "should_deploy_true_default_zrs_creates_resources" {
  command = plan

  variables {
    resource_prefix = run.setup.resource_prefix
    environment     = run.setup.environment
    instance        = run.setup.instance
    location        = run.setup.location
    resource_group  = run.setup.resource_group
    should_deploy   = true
  }

  assert {
    condition     = length(azurerm_storage_account.mirror) == 1
    error_message = "Storage account must be created when should_deploy is true"
  }

  assert {
    condition     = length(azurerm_storage_container.web) == 1
    error_message = "Web container must be created when should_deploy is true"
  }

  assert {
    condition     = azurerm_storage_account.mirror[0].account_replication_type == "ZRS"
    error_message = "Storage account replication type must default to ZRS"
  }

  assert {
    condition     = azurerm_storage_account.mirror[0].min_tls_version == "TLS1_2"
    error_message = "Storage account must enforce TLS1_2 minimum"
  }

  assert {
    condition     = azurerm_storage_account.mirror[0].shared_access_key_enabled == false
    error_message = "Storage account must disable shared access keys"
  }

  assert {
    condition     = azurerm_storage_container.web[0].name == "$web"
    error_message = "Web container name must be $web for static website hosting"
  }
}

// ============================================================
// storage_replication_type Override
// ============================================================

run "should_deploy_grs_override" {
  command = plan

  variables {
    resource_prefix          = run.setup.resource_prefix
    environment              = run.setup.environment
    instance                 = run.setup.instance
    location                 = run.setup.location
    resource_group           = run.setup.resource_group
    should_deploy            = true
    storage_replication_type = "GRS"
  }

  assert {
    condition     = azurerm_storage_account.mirror[0].account_replication_type == "GRS"
    error_message = "Storage account replication type must honor storage_replication_type override"
  }
}
