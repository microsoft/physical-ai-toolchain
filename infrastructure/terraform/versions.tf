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
    fabric = {
      source  = "microsoft/fabric"
      version = "~> 1.0"
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

// Microsoft Fabric provider authenticates via FABRIC_TENANT_ID, FABRIC_CLIENT_ID,
// FABRIC_CLIENT_SECRET environment variables. The service principal must be in
// a security group allow-listed under the Fabric tenant admin setting
// "Service principals can use Fabric APIs".
provider "fabric" {}
