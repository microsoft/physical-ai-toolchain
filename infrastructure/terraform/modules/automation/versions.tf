terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 5.1.0, < 5.8.1"
    }
  }
  required_version = ">= 1.9.8, < 2.0"
}
