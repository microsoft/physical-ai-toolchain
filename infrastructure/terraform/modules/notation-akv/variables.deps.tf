/**
 * # Notation AKV Module Dependencies
 * Typed objects passed in from sibling modules (AKS, ACR, key vault, github-oidc).
 */

variable "aks" {
  description = "AKS cluster identifiers used to issue federated credentials for workload-identity signers."
  type = object({
    id              = string
    oidc_issuer_url = string
  })
}

// tflint-ignore: terraform_unused_declarations
variable "acr" {
  description = "Container registry the signing identity will publish to. Reserved for future ACR role-assignment wiring; accepted today to keep the module contract stable."
  type = object({
    id           = string
    login_server = string
  })
}

variable "key_vault" {
  description = "Optional pre-existing Key Vault to host the signing key. When null, the module provisions a Premium HSM vault."
  type = object({
    id        = string
    vault_uri = string
  })
  default = null
}

// tflint-ignore: terraform_unused_declarations
variable "github_oidc" {
  description = "Optional github-oidc module outputs. Reserved for cross-module wiring; not used by this module today."
  type = object({
    uami_id           = string
    uami_client_id    = string
    uami_principal_id = string
  })
  default = null
}
