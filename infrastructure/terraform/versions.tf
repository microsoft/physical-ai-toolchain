terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.1.0, < 5.4.0"
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
    fabric = {
      source  = "microsoft/fabric"
      version = ">= 1.3.0"
    }
  }
  required_version = ">= 1.9.8, < 2.0"
}

provider "azurerm" {
  storage_use_azuread             = true
  partner_id                      = "acce1e78-0375-4637-a593-86aa36dcfeac"
  resource_provider_registrations = "none"

  features {
    enhanced_validation {
      locations          = true
      resource_providers = true
    }
  }
}

provider "azapi" {}
