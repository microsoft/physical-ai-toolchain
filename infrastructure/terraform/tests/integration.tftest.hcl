// Root integration tests
// Validates resource group conditional creation, module instantiation, and variable passthrough

mock_provider "azurerm" {
}
mock_provider "azuread" {
}
mock_provider "azapi" {
}
mock_provider "msgraph" {
}
mock_provider "tls" {
}
mock_provider "random" {
}

override_data {
  target = module.platform.data.azurerm_client_config.current
  values = {
    tenant_id = "00000000-0000-0000-0000-000000000000"
  }
}

// Override sil module to bypass count expressions that depend on platform module try() outputs
override_module {
  target = module.sil
  outputs = {
    aks_subnets = {
      aks = {
        id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.Network/virtualNetworks/vnet-test/subnets/snet-aks"
        name = "snet-aks"
      }
    }
    aks_cluster = {
      id                  = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-test/providers/Microsoft.ContainerService/managedClusters/aks-test"
      name                = "aks-test"
      fqdn                = "aks-test-dns.hcp.westus3.azmk8s.io"
      kubelet_identity    = null
      node_resource_group = "MC_rg-test_aks-test_westus3"
    }
    aks_oidc_issuer_url   = "https://westus3.oic.prod-aks.azure.com/00000000-0000-0000-0000-000000000000/"
    gpu_node_pool_subnets = {}
    node_pools            = {}
    aks_kube_config = {
      host                   = "https://aks.example"
      cluster_ca_certificate = ""
      client_certificate     = ""
      client_key             = ""
      kube_config_raw        = ""
    }
  }
}

run "setup" {
  module {
    source = "./tests/setup"
  }
}

// ============================================================
// Resource Group Conditionals
// ============================================================

run "resource_group_created" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
  }

  assert {
    condition     = length(azurerm_resource_group.this) == 1
    error_message = "Resource group should be created when should_create_resource_group is true"
  }
}

run "resource_group_existing" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = false
  }

  assert {
    condition     = length(azurerm_resource_group.this) == 0
    error_message = "Resource group should not be created when should_create_resource_group is false"
  }

  assert {
    condition     = length(data.azurerm_resource_group.existing) == 1
    error_message = "Existing resource group data source should be used when not creating"
  }
}

// ============================================================
// Resource Group Naming
// ============================================================

run "resource_group_name_default" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
  }

  assert {
    condition     = azurerm_resource_group.this[0].name == "rg-${run.setup.resource_prefix}-${run.setup.environment}-${run.setup.instance}"
    error_message = "Resource group name should follow rg-{prefix}-{env}-{instance} when no override"
  }
}

run "resource_group_name_override" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
    resource_group_name          = "custom-rg"
  }

  assert {
    condition     = azurerm_resource_group.this[0].name == "custom-rg"
    error_message = "Resource group name should use the override value when provided"
  }
}

// ============================================================
// Module Instantiation
// ============================================================

run "platform_module_instantiated" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
  }

  assert {
    condition     = module.platform.virtual_network != null
    error_message = "Platform module should produce virtual_network output"
  }
}

run "sil_module_instantiated" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
  }

  assert {
    condition     = module.sil.aks_cluster != null
    error_message = "SiL module should produce aks_cluster output"
  }
}

// ============================================================
// Microsoft Graph User Lookup Conditionals
// ============================================================

run "msgraph_user_lookup_conditional" {
  command = plan

  variables {
    resource_prefix                         = run.setup.resource_prefix
    environment                             = run.setup.environment
    instance                                = run.setup.instance
    location                                = run.setup.location
    should_create_resource_group            = true
    should_add_current_user_key_vault_admin = true
  }

  assert {
    condition     = length(msgraph_resource_action.current_user) == 1
    error_message = "Microsoft Graph user lookup should exist when key vault admin flag is true"
  }
}
