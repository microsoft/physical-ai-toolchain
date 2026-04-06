// Platform module naming convention tests
// Validates resource names follow {abbreviation}-{prefix}-{env}-{instance} convention

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

run "verify_standard_naming" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  // NSG
  assert {
    condition     = azurerm_network_security_group.main.name == "nsg-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "NSG name must follow nsg-{prefix}-{env}-{instance}"
  }

  // VNet
  assert {
    condition     = azurerm_virtual_network.main.name == "vnet-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "VNet name must follow vnet-{prefix}-{env}-{instance}"
  }

  // Main Subnet
  assert {
    condition     = azurerm_subnet.main.name == "snet-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Main subnet name must follow snet-{prefix}-{env}-{instance}"
  }

  // PE Subnet (enabled by default)
  assert {
    condition     = azurerm_subnet.private_endpoints[0].name == "snet-pe-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "PE subnet name must follow snet-pe-{prefix}-{env}-{instance}"
  }

  // Log Analytics
  assert {
    condition     = azurerm_log_analytics_workspace.main.name == "log-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Log Analytics workspace name must follow log-{prefix}-{env}-{instance}"
  }

  // Application Insights
  assert {
    condition     = azurerm_application_insights.main.name == "ai-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Application Insights name must follow ai-{prefix}-{env}-{instance}"
  }

  // Monitor Workspace (enabled by default)
  assert {
    condition     = azurerm_monitor_workspace.main[0].name == "azmon-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Monitor workspace name must follow azmon-{prefix}-{env}-{instance}"
  }

  // Grafana (enabled by default)
  assert {
    condition     = azurerm_dashboard_grafana.main[0].name == "graf-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Grafana name must follow graf-{prefix}-{env}-{instance}"
  }

  // DCE (enabled by default)
  assert {
    condition     = azurerm_monitor_data_collection_endpoint.main[0].name == "dce-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "DCE name must follow dce-{prefix}-{env}-{instance}"
  }

  // AMPLS (enabled by default with PE)
  assert {
    condition     = azurerm_monitor_private_link_scope.main[0].name == "ampls-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "AMPLS name must follow ampls-{prefix}-{env}-{instance}"
  }

  // ML Identity
  assert {
    condition     = azurerm_user_assigned_identity.ml.name == "id-ml-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "ML identity name must follow id-ml-{prefix}-{env}-{instance}"
  }

  // OSMO Identity (enabled by default)
  assert {
    condition     = azurerm_user_assigned_identity.osmo[0].name == "id-osmo-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "OSMO identity name must follow id-osmo-{prefix}-{env}-{instance}"
  }

  // AzureML Workspace
  assert {
    condition     = azapi_resource.ml_workspace.name == "mlw-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "ML workspace name must follow mlw-{prefix}-{env}-{instance}"
  }

  // DNS Resolver (enabled by default with PE)
  assert {
    condition     = azurerm_private_dns_resolver.main[0].name == "dnspr-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "DNS resolver name must follow dnspr-{prefix}-{env}-{instance}"
  }
}

run "verify_no_hyphen_naming" {
  command = plan

  variables {
    resource_prefix  = run.setup.resource_prefix
    environment      = run.setup.environment
    instance         = run.setup.instance
    location         = run.setup.location
    resource_group   = run.setup.resource_group
    current_user_oid = run.setup.current_user_oid
  }

  // Key Vault (no hyphens)
  assert {
    condition     = azurerm_key_vault.main.name == "kv${run.setup.resource_prefix}${run.setup.environment}${run.setup.instance}"
    error_message = "Key Vault name must follow kv{prefix}{env}{instance} (no hyphens)"
  }

  // ACR (no hyphens)
  assert {
    condition     = azurerm_container_registry.main.name == "acr${run.setup.resource_prefix}${run.setup.environment}${run.setup.instance}"
    error_message = "ACR name must follow acr{prefix}{env}{instance} (no hyphens)"
  }

  // Storage Account (no hyphens)
  assert {
    condition     = azurerm_storage_account.main.name == "st${run.setup.resource_prefix}${run.setup.environment}${run.setup.instance}"
    error_message = "Storage account name must follow st{prefix}{env}{instance} (no hyphens)"
  }
}

run "verify_nat_gateway_naming" {
  command = plan

  variables {
    resource_prefix           = run.setup.resource_prefix
    environment               = run.setup.environment
    instance                  = run.setup.instance
    location                  = run.setup.location
    resource_group            = run.setup.resource_group
    current_user_oid          = run.setup.current_user_oid
    should_enable_nat_gateway = true
  }

  // NAT Gateway Public IP
  assert {
    condition     = azurerm_public_ip.nat_gateway[0].name == "pip-ng-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "NAT Gateway public IP name must follow pip-ng-{prefix}-{env}-{instance}"
  }

  // NAT Gateway
  assert {
    condition     = azurerm_nat_gateway.main[0].name == "ng-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "NAT Gateway name must follow ng-{prefix}-{env}-{instance}"
  }
}

run "verify_private_endpoint_naming" {
  command = plan

  variables {
    resource_prefix                = run.setup.resource_prefix
    environment                    = run.setup.environment
    instance                       = run.setup.instance
    location                       = run.setup.location
    resource_group                 = run.setup.resource_group
    current_user_oid               = run.setup.current_user_oid
    should_enable_private_endpoint = true
  }

  // ACR PE
  assert {
    condition     = azurerm_private_endpoint.acr[0].name == "pe-acr-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "ACR PE name must follow pe-acr-{prefix}-{env}-{instance}"
  }

  // Key Vault PE
  assert {
    condition     = azurerm_private_endpoint.key_vault[0].name == "pe-kv-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Key Vault PE name must follow pe-kv-{prefix}-{env}-{instance}"
  }

  // Storage Blob PE
  assert {
    condition     = azurerm_private_endpoint.storage_blob[0].name == "pe-blob-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Storage blob PE name must follow pe-blob-{prefix}-{env}-{instance}"
  }

  // Storage File PE
  assert {
    condition     = azurerm_private_endpoint.storage_file[0].name == "pe-file-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Storage file PE name must follow pe-file-{prefix}-{env}-{instance}"
  }

  // ML API PE
  assert {
    condition     = azurerm_private_endpoint.azureml_api[0].name == "pe-ml-api-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "ML API PE name must follow pe-ml-api-{prefix}-{env}-{instance}"
  }

  // Monitor PE (requires AMPLS to also be enabled — it is by default)
  assert {
    condition     = azurerm_private_endpoint.monitor[0].name == "pe-monitor-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Monitor PE name must follow pe-monitor-{prefix}-{env}-{instance}"
  }
}
