/**
 * # ARC Runners Variables
 * Module-specific inputs for installing the GitHub Actions Runner Controller (ARC)
 * gha-runner-scale-set on the existing AKS cluster.
 */

variable "github_config_url" {
  description = "GitHub repository or organization URL the runner scale set is registered against (e.g. https://github.com/<owner>/<repo>)."
  type        = string
}

variable "github_app_id" {
  description = "GitHub App ID used by the runner scale set listener for authentication."
  type        = string
}

variable "github_app_installation_id" {
  description = "Installation ID of the GitHub App in the target organization or repository."
  type        = string
}

// tflint-ignore: terraform_unused_declarations
variable "github_app_private_key_secret_id" {
  description = "Optional Azure Key Vault secret URI containing the GitHub App private key (PEM). Reserved for callers that pre-provision the secret out-of-band; the module references the secret by name (`github-app-private-key`) inside var.key_vault today."
  type        = string
  default     = null
  sensitive   = true
}

variable "runner_image_tag" {
  description = "Container image tag for the runner scale set runners."
  type        = string
  default     = "latest"
}

variable "runner_replicas" {
  description = "Minimum number of runner pods kept warm by the scale set."
  type        = number
  default     = 2
}

variable "should_enable_sigstore_egress" {
  description = "When true, install a NetworkPolicy allowlist permitting runner egress to Sigstore (Fulcio, Rekor, TUF), GitHub, ACR, and Key Vault endpoints required for keyless signing."
  type        = bool
  default     = true
}
