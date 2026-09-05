terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.1.0, < 5.4.0"
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
