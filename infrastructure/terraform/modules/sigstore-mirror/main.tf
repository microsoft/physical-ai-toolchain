/**
 * # Sigstore Mirror Module
 *
 * Optional Storage Account static website that serves an air-gapped Sigstore TUF mirror
 * (https://docs.sigstore.dev/cosign/system_config/airgapped). Consumers that rely on the
 * public Sigstore good instance do not need this module; deploy it only when builders or
 * verifiers must operate without egress to fulcio.sigstore.dev / rekor.sigstore.dev.
 *
 * The module provisions the storage surface only. A separate scheduled job (cron noted in
 * the `refresh_schedule_cron` tag) is responsible for syncing TUF metadata into `$web`.
 */

locals {
  storage_account_name = substr(lower(replace("stsigmirror${var.resource_prefix}${var.environment}${var.instance}", "-", "")), 0, 24)

  base_tags = {
    component             = "sigstore-mirror"
    environment           = var.environment
    resource_prefix       = var.resource_prefix
    instance              = var.instance
    refresh_schedule_cron = var.refresh_schedule_cron
  }

  tags = merge(local.base_tags, var.tags)
}

// ============================================================
// Storage Account (static website hosting the TUF mirror)
// ============================================================

resource "azurerm_storage_account" "mirror" {
  count = var.should_deploy ? 1 : 0

  name                     = local.storage_account_name
  resource_group_name      = var.resource_group.name
  location                 = var.resource_group.location
  account_tier             = "Standard"
  account_replication_type = var.storage_replication_type
  account_kind             = "StorageV2"

  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = true
  shared_access_key_enabled       = false

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }
  }

  static_website {
    index_document     = "index.html"
    error_404_document = "404.html"
  }

  tags = local.tags
}

// ============================================================
// Web container (created automatically as $web; tracked for outputs)
// ============================================================

resource "azurerm_storage_container" "web" {
  count = var.should_deploy ? 1 : 0

  name                  = "$web"
  storage_account_id    = azurerm_storage_account.mirror[0].id
  container_access_type = "blob"
}
