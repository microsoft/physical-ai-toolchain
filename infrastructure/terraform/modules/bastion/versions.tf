// Provider requirements for Bastion module
// Provider blocks are defined in standalone deployments and CI only

terraform {
  required_version = ">= 1.9.8, < 2.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.1.0, < 5.9.1"
    }
  }
}
