/**
 * # Microsoft Fabric Capacity and Workspace
 *
 * Provisions an azurerm_fabric_capacity (gated by should_create_fabric_capacity)
 * and a fabric_workspace via the microsoft/fabric provider.
 *
 * The fabric_workspace resource's capacity_id is the Fabric capacity GUID (not
 * the ARM resource ID). The azurerm_fabric_capacity resource does not expose
 * the GUID directly; callers must supply it via fabric_capacity_uuid after the
 * first apply (or skip workspace creation entirely by setting
 * should_create_fabric_workspace = false). See README "Two-pass deployment".
 *
 * Authentication for the microsoft/fabric provider flows from environment
 * variables FABRIC_TENANT_ID, FABRIC_CLIENT_ID, FABRIC_CLIENT_SECRET. The
 * service principal must be in a security group that is allow-listed in the
 * Fabric tenant admin setting "Service principals can use Fabric APIs".
 */

resource "azurerm_fabric_capacity" "this" {
  count = var.should_create_fabric_capacity ? 1 : 0

  name                = "fc${var.resource_prefix}${var.environment}${var.instance}"
  resource_group_name = var.resource_group.name
  location            = local.location

  administration_members = var.fabric_admin_members

  sku {
    name = var.fabric_capacity_sku
    tier = "Fabric"
  }
}

resource "fabric_workspace" "this" {
  count = var.should_create_fabric_workspace && var.fabric_capacity_uuid != null ? 1 : 0

  display_name = "fws-${local.resource_name_suffix}"
  description  = "Conversion pipeline workspace (${var.environment})"
  capacity_id  = var.fabric_capacity_uuid
}
