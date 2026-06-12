/**
 * # Notation AKV Module
 *
 * Provisions an Azure Key Vault Premium HSM-backed signing key and a
 * workload-identity-federated user-assigned managed identity used by Notation
 * v1 to sign container images. All resources are gated by `var.should_deploy`
 * so this module is a no-op when `signing_mode != "notation"`.
 *
 * When `var.key_vault` is supplied, the module reuses that vault and only
 * creates the signing key, identity, role assignment, and federated credentials.
 * Otherwise the module provisions a new Premium HSM Key Vault.
 */

data "azurerm_client_config" "current" {}

locals {
  resource_name_suffix = "${var.resource_prefix}-${var.environment}-${var.instance}"
  tenant_id            = data.azurerm_client_config.current.tenant_id

  // Provision a new Key Vault only when the caller did not pass one in.
  should_create_key_vault = var.key_vault == null && var.should_deploy

  // Resolved Key Vault id used by downstream resources (key + role assignment).
  key_vault_id  = var.key_vault != null ? var.key_vault.id : try(azurerm_key_vault.notation[0].id, null)
  key_vault_uri = var.key_vault != null ? var.key_vault.vault_uri : try(azurerm_key_vault.notation[0].vault_uri, null)

  // Key Vault name capped at 24 chars per Azure naming limits.
  key_vault_name = substr("kv-not-${replace(local.resource_name_suffix, "-", "")}", 0, 24)
}

// ============================================================================
// Key Vault (Premium, HSM-backed) - only when caller did not provide one.
// ============================================================================

resource "azurerm_key_vault" "notation" {
  count = local.should_create_key_vault ? 1 : 0

  name                = local.key_vault_name
  resource_group_name = var.resource_group.name
  location            = var.location
  tenant_id           = local.tenant_id
  sku_name            = var.key_vault_sku

  purge_protection_enabled   = var.purge_protection_enabled
  soft_delete_retention_days = var.soft_delete_retention_days

  rbac_authorization_enabled = true
}

// ============================================================================
// Signing Key (RSA-HSM 3072 by default, EC-HSM P-384 supported).
// ============================================================================

resource "azurerm_key_vault_key" "notation_signing" {
  count = var.should_deploy ? 1 : 0

  name         = "notation-signing-${local.resource_name_suffix}"
  key_vault_id = local.key_vault_id
  key_type     = var.key_algorithm
  key_size     = var.key_algorithm == "RSA-HSM" ? var.key_size : null
  curve        = var.key_algorithm == "EC-HSM" ? var.key_curve : null

  key_opts = ["sign", "verify"]

  depends_on = [azurerm_key_vault.notation]
}

// ============================================================================
// Workload Identity for Notation signing jobs.
// ============================================================================

resource "azurerm_user_assigned_identity" "notation_signer" {
  count = var.should_deploy ? 1 : 0

  name                = "id-notation-signer-${local.resource_name_suffix}"
  resource_group_name = var.resource_group.name
  location            = var.location
}

// ============================================================================
// Role assignment: signer UAMI -> Key Vault Crypto User on the signing vault.
// ============================================================================

resource "azurerm_role_assignment" "notation_signer_crypto_user" {
  count = var.should_deploy ? 1 : 0

  scope                = local.key_vault_id
  role_definition_name = "Key Vault Crypto User"
  principal_id         = azurerm_user_assigned_identity.notation_signer[0].principal_id
}

// ============================================================================
// Federated identity credentials: one per authorised subject claim.
// ============================================================================

resource "azurerm_federated_identity_credential" "notation_signer" {
  for_each = var.should_deploy ? toset(var.signer_subject_claims) : toset([])

  name                = "fc-notation-${local.resource_name_suffix}-${substr(sha256(each.value), 0, 8)}"
  resource_group_name = var.resource_group.name
  parent_id           = azurerm_user_assigned_identity.notation_signer[0].id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = var.aks.oidc_issuer_url
  subject             = each.value
}
