/**
 * # Notation AKV Variables
 * Module-specific inputs for provisioning the AKV-backed Notation v1 signing
 * identity used by the consumer-side container supply-chain pipeline.
 */

variable "signer_subject_claims" {
  description = "Federated credential subject claims authorised to sign with the AKV key (GitHub OIDC and/or AKS workload identity subjects)."
  type        = list(string)
}

variable "should_deploy" {
  description = "Whether to provision the Notation signing identity. Set to true when signing_mode = \"notation\"."
  type        = bool
  default     = false
}

variable "key_algorithm" {
  description = "Algorithm for the AKV signing key. Supported values: RSA-HSM, EC-HSM."
  type        = string
  default     = "RSA-HSM"
}

variable "key_size" {
  description = "Key size in bits for RSA-HSM keys. Ignored for EC-HSM."
  type        = number
  default     = 3072
}

variable "key_curve" {
  description = "Curve name for EC-HSM keys (e.g. P-384). Ignored for RSA-HSM."
  type        = string
  default     = "P-384"
}

variable "key_vault_sku" {
  description = "SKU for any Key Vault created by this module. Premium is required for HSM-backed keys."
  type        = string
  default     = "premium"
}

variable "purge_protection_enabled" {
  description = "Whether purge protection is enabled on a Key Vault created by this module."
  type        = bool
  default     = true
}

variable "soft_delete_retention_days" {
  description = "Soft-delete retention window (in days) applied to a Key Vault created by this module."
  type        = number
  default     = 90
}
