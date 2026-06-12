// Root signing-mode matrix tests
// Validates conditional instantiation of github_oidc, notation_akv, arc_runners, and sigstore_mirror
// modules across signing_mode values (sigstore, notation, none) and the should_deploy_sigstore_mirror toggle.

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
mock_provider "helm" {
}
mock_provider "kubernetes" {
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
// signing_mode = "sigstore" with mirror enabled
// ============================================================

run "sigstore_mode_with_mirror" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    should_create_resource_group  = true
    signing_mode                  = "sigstore"
    should_deploy_sigstore_mirror = true
  }

  assert {
    condition     = length(module.github_oidc) == 1
    error_message = "github_oidc module must be instantiated when signing_mode != none"
  }

  assert {
    condition     = length(module.arc_runners) == 1
    error_message = "arc_runners module must be instantiated when signing_mode != none"
  }

  assert {
    condition     = length(module.notation_akv) == 0
    error_message = "notation_akv module must not be instantiated when signing_mode != notation"
  }

  assert {
    condition     = length(module.sigstore_mirror) == 1
    error_message = "sigstore_mirror module must be instantiated when signing_mode = sigstore and should_deploy_sigstore_mirror = true"
  }
}

// ============================================================
// signing_mode = "sigstore" using public Rekor (mirror disabled)
// ============================================================

run "sigstore_mode_public_rekor" {
  command = plan

  variables {
    resource_prefix               = run.setup.resource_prefix
    environment                   = run.setup.environment
    instance                      = run.setup.instance
    location                      = run.setup.location
    should_create_resource_group  = true
    signing_mode                  = "sigstore"
    should_deploy_sigstore_mirror = false
  }

  assert {
    condition     = length(module.github_oidc) == 1
    error_message = "github_oidc module must be instantiated when signing_mode != none"
  }

  assert {
    condition     = length(module.arc_runners) == 1
    error_message = "arc_runners module must be instantiated when signing_mode != none"
  }

  assert {
    condition     = length(module.sigstore_mirror) == 0
    error_message = "sigstore_mirror module must not be instantiated when should_deploy_sigstore_mirror = false"
  }
}

// ============================================================
// signing_mode = "notation"
// ============================================================

run "notation_mode" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
    signing_mode                 = "notation"
  }

  assert {
    condition     = length(module.github_oidc) == 1
    error_message = "github_oidc module must be instantiated when signing_mode != none"
  }

  assert {
    condition     = length(module.arc_runners) == 1
    error_message = "arc_runners module must be instantiated when signing_mode != none"
  }

  assert {
    condition     = length(module.notation_akv) == 1
    error_message = "notation_akv module must be instantiated when signing_mode = notation"
  }

  assert {
    condition     = length(module.sigstore_mirror) == 0
    error_message = "sigstore_mirror module must not be instantiated when signing_mode != sigstore"
  }
}

// ============================================================
// signing_mode = "none"
// ============================================================

run "none_mode" {
  command = plan

  variables {
    resource_prefix              = run.setup.resource_prefix
    environment                  = run.setup.environment
    instance                     = run.setup.instance
    location                     = run.setup.location
    should_create_resource_group = true
    signing_mode                 = "none"
  }

  assert {
    condition     = length(module.github_oidc) == 0
    error_message = "github_oidc module must not be instantiated when signing_mode = none"
  }

  assert {
    condition     = length(module.arc_runners) == 0
    error_message = "arc_runners module must not be instantiated when signing_mode = none"
  }

  assert {
    condition     = length(module.notation_akv) == 0
    error_message = "notation_akv module must not be instantiated when signing_mode = none"
  }

  assert {
    condition     = length(module.sigstore_mirror) == 0
    error_message = "sigstore_mirror module must not be instantiated when signing_mode = none"
  }
}
