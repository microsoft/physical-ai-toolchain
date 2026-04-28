/**
 * # Microsoft Fabric Capacity and Workspace
 *
 * Provisions an azurerm_fabric_capacity (gated by should_create_fabric_capacity)
 * and a fabric_workspace via the microsoft/fabric provider.
 *
 * The fabric_workspace resource's capacity_id is the Fabric capacity GUID (not
 * the ARM resource ID). The azurerm_fabric_capacity resource does not expose
 * the GUID directly, so the GUID is discovered at apply time via a deferred
 * data "fabric_capacity" lookup keyed on the capacity display name. The
 * terraform_data shim defers data-source evaluation past plan so a single
 * `terraform apply` provisions both the capacity and the workspace.
 *
 * Authentication for the microsoft/fabric provider falls back to Azure CLI
 * (`az login`) when no provider block is declared. The signed-in operator
 * identity must be in a security group allow-listed under the Fabric tenant
 * admin setting "Service principals can use Fabric APIs" (or the equivalent
 * user-context setting).
 */

locals {
  // Fabric Capacity name must match ^[a-z][a-z0-9]{2,62}$ (lowercase letters and digits only —
  // no hyphens). The hyphenated `{abbreviation}-{prefix}-{environment}-{instance}` convention
  // cannot apply here, so `fc` joins `kv`, `st`, `acr` as a no-hyphen exception.
  fabric_capacity_name = "fc${var.resource_prefix}${var.environment}${var.instance}"

  fabric_capacity_id = try(
    data.fabric_capacity.created[0].id,
    data.fabric_capacity.existing[0].id,
    null,
  )
}

resource "azurerm_fabric_capacity" "this" {
  count = var.should_create_fabric_capacity ? 1 : 0

  name                = local.fabric_capacity_name
  resource_group_name = var.resource_group.name
  location            = local.location

  administration_members = var.fabric_admin_members

  sku {
    name = var.fabric_capacity_sku
    tier = "Fabric"
  }
}

// Defer data-source evaluation past `terraform plan` so the GUID is read only after
// the capacity has been created. terraform_data wraps the display_name input and
// declares the dependency on the azurerm_fabric_capacity resource.
resource "terraform_data" "defer_fabric_capacity_created" {
  count = var.should_create_fabric_capacity ? 1 : 0

  input = {
    display_name = local.fabric_capacity_name
  }

  depends_on = [azurerm_fabric_capacity.this]
}

data "fabric_capacity" "created" {
  count = length(terraform_data.defer_fabric_capacity_created)

  display_name = terraform_data.defer_fabric_capacity_created[0].output.display_name
}

// When operating against a pre-existing capacity, defer the lookup the same way
// so the workspace can resolve capacity_id without a two-pass apply.
resource "terraform_data" "defer_fabric_capacity_existing" {
  count = var.should_create_fabric_capacity ? 0 : (var.should_create_fabric_workspace ? 1 : 0)

  input = {
    display_name = local.fabric_capacity_name
  }
}

data "fabric_capacity" "existing" {
  count = length(terraform_data.defer_fabric_capacity_existing)

  display_name = terraform_data.defer_fabric_capacity_existing[0].output.display_name
}

resource "fabric_workspace" "this" {
  count = var.should_create_fabric_workspace ? 1 : 0

  display_name = "fws-${local.resource_name_suffix}"
  description  = "Conversion pipeline workspace (${var.environment})"
  capacity_id  = local.fabric_capacity_id
}
