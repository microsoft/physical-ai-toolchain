terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.51.0"
    }
  }
  required_version = ">= 1.9.8, < 2.0"
}

provider "azurerm" {
  storage_use_azuread = true
  partner_id          = "acce1e78-0375-4637-a593-86aa36dcfeac"
  features {}
}
