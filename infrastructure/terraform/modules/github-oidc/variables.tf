/**
 * # GitHub OIDC Variables
 * Inputs identifying the GitHub repository, the federated subject set, and role-assignment toggles.
 */

// tflint-ignore: terraform_unused_declarations
variable "github_owner" {
  description = "GitHub organization or user that owns the repository hosting the publish workflows. Documented contract surface; consumers reference this when constructing federated_subjects values."
  type        = string
}

// tflint-ignore: terraform_unused_declarations
variable "github_repo" {
  description = "GitHub repository name (without the owner prefix). Documented contract surface; consumers reference this when constructing federated_subjects values."
  type        = string
}

variable "federated_subjects" {
  description = <<-EOT
    Map of GitHub OIDC subject claims to bind to the user-assigned managed identity.
    The map key becomes the federated credential resource name (suffixed with `-fic`).
    Values must be exact OIDC subjects (no wildcards), for example:
      tags-publish    = "repo:${"$"}{github_owner}/${"$"}{github_repo}:ref:refs/tags/v*"  // illustrative; use exact ref or environment claim
      main-build      = "repo:${"$"}{github_owner}/${"$"}{github_repo}:ref:refs/heads/main"
      production-env  = "repo:${"$"}{github_owner}/${"$"}{github_repo}:environment:production"
  EOT
  type        = map(string)
}

variable "should_grant_acr_push" {
  description = "When true and var.acr is supplied, assigns AcrPush on the consumed registry."
  type        = bool
  default     = true
}
