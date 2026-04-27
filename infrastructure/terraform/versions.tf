terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.51.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = ">= 3.0.2"
    }
    azapi = {
      source  = "Azure/azapi"
      version = ">= 2.3.0"
    }
    msgraph = {
      source  = "microsoft/msgraph"
      version = ">= 0.2.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = ">= 4.0.6"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.17.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.35.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
  required_version = ">= 1.9.8, < 2.0"
}

provider "azurerm" {
  storage_use_azuread = true
  partner_id          = "acce1e78-0375-4637-a593-86aa36dcfeac"
  features {}
}

provider "azapi" {}

provider "kubernetes" {
  host                   = try(module.sil.aks_kube_config.host, null)
  cluster_ca_certificate = try(base64decode(module.sil.aks_kube_config.cluster_ca_certificate), null)
  client_certificate     = try(base64decode(module.sil.aks_kube_config.client_certificate), null)
  client_key             = try(base64decode(module.sil.aks_kube_config.client_key), null)
}

provider "helm" {
  kubernetes = {
    host                   = try(module.sil.aks_kube_config.host, null)
    cluster_ca_certificate = try(base64decode(module.sil.aks_kube_config.cluster_ca_certificate), null)
    client_certificate     = try(base64decode(module.sil.aks_kube_config.client_certificate), null)
    client_key             = try(base64decode(module.sil.aks_kube_config.client_key), null)
  }
}
