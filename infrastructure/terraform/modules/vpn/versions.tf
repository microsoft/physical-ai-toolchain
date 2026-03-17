// Provider requirements for VPN module
// Provider blocks are defined in blueprints/ci only

terraform {
  required_version = ">= 1.9.8, < 2.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.51.0"
    }
  }
}
