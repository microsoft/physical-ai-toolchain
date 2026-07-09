// Root output structure tests
// Validates output presence and nullability when features are disabled

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

variables {
  aml_managed_network_isolation_mode = "Disabled"
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
    should_deploy_aks            = false
    aml_compute_clusters         = {}
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
    condition     = length(output.aml_compute_clusters) == 0
    error_message = "aml_compute_clusters output should be empty when no AML compute clusters are configured"
  }
}

run "aml_compute_clusters_output_populated" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
    should_deploy_aks            = false
    aml_compute_clusters = {
      gpu-cluster = {
        vm_size                   = "Standard_NC4as_T4_v3"
        vm_priority               = "LowPriority"
        min_node_count            = 0
        max_node_count            = 1
        scale_down_after_idle     = "PT5M"
        node_public_ip_enabled    = false
        ssh_public_access_enabled = false
        identity_type             = "UserAssigned"
      }
    }
  }

  assert {
    condition     = length(output.aml_compute_clusters) == 1
    error_message = "root aml_compute_clusters output should include configured clusters"
  }

  assert {
    condition     = output.aml_compute_clusters["gpu-cluster"].name == "gpu-cluster"
    error_message = "root aml_compute_clusters output should forward platform output values keyed by cluster name"
  }

  assert {
    condition     = length(keys(output.aml_compute_clusters["gpu-cluster"])) == 2 && contains(keys(output.aml_compute_clusters["gpu-cluster"]), "id") && contains(keys(output.aml_compute_clusters["gpu-cluster"]), "name")
    error_message = "root aml_compute_clusters output values should expose only id and name"
  }

  assert {
    condition     = output.aks_cluster == null
    error_message = "aks_cluster output should be null when AKS is disabled"
  }

  assert {
    condition     = output.aks_oidc_issuer_url == null
    error_message = "aks_oidc_issuer_url output should be null when AKS is disabled"
  }

  assert {
    condition     = output.gpu_node_pool_subnets == null
    error_message = "gpu_node_pool_subnets output should be null when AKS is disabled"
  }

  assert {
    condition     = output.node_pools == null
    error_message = "node_pools output should be null when AKS is disabled"
  }
}

