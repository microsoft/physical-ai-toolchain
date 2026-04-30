/**
 * # ARC Runners Module Dependencies
 * Typed objects passed in from sibling modules (AKS, ACR, github-oidc, key vault).
 */

variable "aks" {
  description = "AKS cluster the ARC controller and runner scale set are deployed into."
  type = object({
    id                     = string
    oidc_issuer_url        = string
    host                   = string
    cluster_ca_certificate = string
    kube_config_raw        = string
  })
  sensitive = true
}

variable "acr" {
  description = "Azure Container Registry the runners publish signed images to."
  type = object({
    id           = string
    login_server = string
  })
}

// tflint-ignore: terraform_unused_declarations
variable "github_oidc" {
  description = "Optional github-oidc module outputs. Reserved for future runner workload-identity federation; the module provisions its own dedicated UAMI today."
  type = object({
    uami_id           = string
    uami_client_id    = string
    uami_principal_id = string
  })
  default = null
}

variable "key_vault" {
  description = "Azure Key Vault hosting the GitHub App private key secret consumed by the runner controller."
  type = object({
    id        = string
    vault_uri = string
  })
}
