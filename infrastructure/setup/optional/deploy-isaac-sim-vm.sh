#!/usr/bin/env bash
# Deploy Isaac Sim VM with Terraform-derived infrastructure defaults
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck disable=SC1091
# shellcheck source=../../../shared/lib/common.sh
source "$REPO_ROOT/shared/lib/common.sh"
# shellcheck disable=SC1091
# shellcheck source=infrastructure/setup/defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Deploy one or more Isaac Sim VMs using the Bicep template in
infrastructure/setup/optional/isaac-sim-vm/bicep/. By default, the script reads the
resource group, location, dedicated Isaac Sim VM subnet, and shared NSG from
Terraform outputs. When Terraform state is unavailable, pass --tfvars-file to
derive the standard resource names from a Terraform variables file and resolve
the subnet and NSG IDs from Azure.

OPTIONS:
    -h, --help                  Show this help message
    -t, --tf-dir DIR            Terraform directory (default: $DEFAULT_TF_DIR)
    --tfvars-file PATH          Terraform tfvars file used when tfstate is unavailable
    --vm-name NAME              VM name to deploy
    --resource-group NAME       Target resource group override
    --isolated-vm-rg            Create and use a derived VM-specific resource group
    --location LOCATION         Azure location override
    --subnet-id ID              Existing subnet resource ID override
    --nsg-id ID                 Existing network security group resource ID override
    --admin-username NAME       Admin username (default: azureuser)
    --admin-password VALUE      Admin password override
    --vm-size SIZE              VM size (default: Standard_NV36ads_A10_v5)
    --disable-encryption-at-host
             Disable EncryptionAtHost for unsupported VM sizes or regions
    --spot-vm                   Deploy the VM as Azure Spot capacity for testing
    --spot-eviction-policy POLICY
             Spot eviction policy for --spot-vm: Deallocate or Delete (default: Deallocate)
    --deployment-name NAME      ARM deployment name (default: isaac-lab-vms)
    --skip-marketplace-requirements
                   Skip acceptance of the Isaac Sim marketplace terms
    --enable-mde-linux          Deploy the Defender for Endpoint extension with defaults
    --config-preview            Print configuration and exit

EXAMPLES:
    $(basename "$0") --vm-name isaac-sim-dev-01
    $(basename "$0") --vm-name isaac-sim-dev-01 --isolated-vm-rg
    $(basename "$0") --tfvars-file infrastructure/terraform/terraform.tfvars --vm-name isaac-sim-dev-01
    $(basename "$0") --vm-name isaac-sim-dev-01 --subnet-id <subnet-id> --nsg-id <nsg-id>
EOF
}

trim_whitespace() {
  local value="${1-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "$value"
}

resolve_file_path() {
  local file_path="${1:?file path required}"

  if [[ "$file_path" = /* ]]; then
    printf '%s\n' "$file_path"
    return 0
  fi

  printf '%s/%s\n' "$(cd "$(dirname "$file_path")" && pwd)" "$(basename "$file_path")"
}

read_tfvars_metadata() {
  local tf_dir="${1:?terraform directory required}"
  local tfvars_path="${2:?terraform variables file required}"
  local metadata=""

  [[ -f "$tfvars_path" ]] || fatal "Terraform variables file not found: $tfvars_path"
  [[ -d "$tf_dir" ]] || fatal "Terraform directory not found: $tf_dir"

  info "Initializing Terraform in $tf_dir for tfvars evaluation..."
  terraform -chdir="$tf_dir" init -backend=false -input=false -no-color >/dev/null || fatal "Unable to initialize Terraform in $tf_dir"

  metadata=$(terraform -chdir="$tf_dir" console -var-file="$tfvars_path" <<'EOF'
jsonencode({
  environment = var.environment
  instance = var.instance
  location = var.location
  resource_group_name = var.resource_group_name
  resource_prefix = var.resource_prefix
  should_create_vm_subnet = var.should_create_vm_subnet
})
EOF
  ) || fatal "Unable to evaluate Terraform variables from $tfvars_path"

  jq -r '.' <<< "$metadata"
}

print_az_command() {
  local -a command=("$@")
  local index

  for index in "${!command[@]}"; do
    if [[ "${command[$index]}" == adminPassword=* ]]; then
      command[index]='adminPassword=<redacted>'
    fi
  done

  printf '%q ' az "${command[@]}"
  echo
}

derive_vm_resource_group_from_tfvars() {
  local resource_prefix="${1-}"
  local environment="${2-}"
  local instance="${3-}"

  if [[ -z "$resource_prefix" || -z "$environment" || -z "$instance" ]]; then
    return 1
  fi

  printf 'rg-%s-virtual-machines-%s-%s\n' "$resource_prefix" "$environment" "$instance"
}

derive_vm_resource_group_from_main_rg() {
  local main_resource_group="${1:?main resource group required}"

  if [[ "$main_resource_group" =~ ^rg-(.+)-([^-]+)-([^-]+)$ ]]; then
    printf 'rg-%s-virtual-machines-%s-%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
    return 0
  fi

  return 1
}


repo_root="$(cd "$SCRIPT_DIR/../../.." && pwd)"
template_file="$repo_root/infrastructure/setup/optional/isaac-sim-vm/bicep/main.bicep"

# Defaults
tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
tfvars_file=""
resource_group=""
vm_resource_group=""
location=""
subnet_id=""
nsg_id=""
admin_username="azureuser"
admin_password="${ISAAC_LAB_VM_ADMIN_PASSWORD:-}"
vm_size="Standard_NV36ads_A10_v5"
should_enable_encryption_at_host=true
use_spot_vm=false
spot_eviction_policy="Deallocate"
spot_eviction_policy_explicit=false
deployment_name="isaac-lab-vms"
install_marketplace_requirements=true
isolated_vm_rg=false
config_preview=false
enable_mde_linux=false
vm_name=""
tfvars_metadata='{}'
tfvars_environment=""
tfvars_instance="001"
tfvars_location=""
tfvars_resource_group_name=""
tfvars_resource_prefix=""
tfvars_should_create_vm_subnet="false"
tfvars_resource_name_suffix=""
tfvars_vm_subnet_name=""
tfvars_vnet_name=""
tfvars_nsg_name=""
marketplace_publisher="nvidia"
marketplace_offer="isaac_sim_developer_workstation"
marketplace_plan="isaac_sim_developer_workstation_community_linux"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)             show_help; exit 0 ;;
    -t|--tf-dir)           tf_dir="$2"; shift 2 ;;
    --tfvars-file)         tfvars_file="$2"; shift 2 ;;
    --vm-name)             vm_name="$2"; shift 2 ;;
    --resource-group)      resource_group="$2"; shift 2 ;;
    --isolated-vm-rg)      isolated_vm_rg=true; shift ;;
    --location)            location="$2"; shift 2 ;;
    --subnet-id)           subnet_id="$2"; shift 2 ;;
    --nsg-id)              nsg_id="$2"; shift 2 ;;
    --admin-username)      admin_username="$2"; shift 2 ;;
    --admin-password)      admin_password="$2"; shift 2 ;;
    --vm-size)             vm_size="$2"; shift 2 ;;
    --disable-encryption-at-host) should_enable_encryption_at_host=false; shift ;;
    --spot-vm)             use_spot_vm=true; shift ;;
    --spot-eviction-policy) spot_eviction_policy="$2"; spot_eviction_policy_explicit=true; shift 2 ;;
    --deployment-name)     deployment_name="$2"; shift 2 ;;
    --install-marketplace-requirements) install_marketplace_requirements=true; shift ;;
    --skip-marketplace-requirements) install_marketplace_requirements=false; shift ;;
    --enable-mde-linux)    enable_mde_linux=true; shift ;;
    --config-preview)      config_preview=true; shift ;;
    *)                     fatal "Unknown option: $1" ;;
  esac
done

require_tools az jq terraform

case "$spot_eviction_policy" in
  Deallocate|Delete) ;;
  *) fatal "Invalid --spot-eviction-policy value: $spot_eviction_policy. Use Deallocate or Delete." ;;
esac

if [[ "$spot_eviction_policy_explicit" == "true" && "$use_spot_vm" != "true" ]]; then
  fatal "--spot-eviction-policy requires --spot-vm. Omit the eviction policy or add --spot-vm."
fi

[[ -f "$template_file" ]] || fatal "Bicep template not found: $template_file"

if [[ -n "$tfvars_file" ]]; then
  tfvars_file=$(resolve_file_path "$tfvars_file")
  info "Reading Terraform variables from $tfvars_file..."
  tfvars_metadata=$(read_tfvars_metadata "$tf_dir" "$tfvars_file")
  tfvars_environment=$(tf_get "$tfvars_metadata" 'environment')
  tfvars_instance=$(tf_get "$tfvars_metadata" 'instance' '001')
  tfvars_location=$(tf_get "$tfvars_metadata" 'location')
  tfvars_resource_group_name=$(tf_get "$tfvars_metadata" 'resource_group_name')
  tfvars_resource_prefix=$(tf_get "$tfvars_metadata" 'resource_prefix')
  tfvars_should_create_vm_subnet=$(tf_get "$tfvars_metadata" 'should_create_vm_subnet' 'false')

  if [[ -n "$tfvars_environment" && -n "$tfvars_resource_prefix" ]]; then
    tfvars_resource_name_suffix="${tfvars_resource_prefix}-${tfvars_environment}-${tfvars_instance}"
    tfvars_vnet_name="vnet-${tfvars_resource_name_suffix}"
    tfvars_vm_subnet_name="snet-isaaclab-vm-${tfvars_resource_name_suffix}"
    tfvars_nsg_name="nsg-${tfvars_resource_name_suffix}"
    tfvars_resource_group_name="${tfvars_resource_group_name:-rg-${tfvars_resource_name_suffix}}"
  fi
fi

tf_output='{}'
if [[ -f "$tf_dir/terraform.tfstate" ]]; then
  info "Reading terraform outputs from $tf_dir..."
  tf_output=$(read_terraform_outputs "$tf_dir")
else
  if [[ -n "$tfvars_file" ]]; then
    warn "terraform.tfstate not found in $tf_dir; using tfvars-derived defaults where possible"
  else
    warn "terraform.tfstate not found in $tf_dir; using explicit CLI overrides only"
  fi
fi

if [[ -z "$resource_group" ]]; then
  resource_group=$(tf_get "$tf_output" "resource_group.value.name")
  if [[ -z "$resource_group" ]]; then
    resource_group="$tfvars_resource_group_name"
  fi
  [[ -n "$resource_group" ]] || fatal "Resource group not provided and not found in terraform outputs"
fi

if [[ -z "$location" ]]; then
  location=$(tf_get "$tf_output" "resource_group.value.location")
  if [[ -z "$location" ]]; then
    location="$tfvars_location"
  fi
fi

if [[ -z "$location" && -n "$resource_group" ]]; then
  info "Resolving resource group location from Azure..."
  location=$(az group show --name "$resource_group" --query location --output tsv)
fi

if [[ "$isolated_vm_rg" == "true" ]]; then
  if vm_resource_group=$(derive_vm_resource_group_from_tfvars "$tfvars_resource_prefix" "$tfvars_environment" "$tfvars_instance"); then
    :
  elif vm_resource_group=$(derive_vm_resource_group_from_main_rg "$resource_group"); then
    :
  else
    fatal "Unable to derive the isolated VM resource group name from $resource_group. Provide --tfvars-file with resource_prefix, environment, and instance values or use a main resource group named like rg-<resource-prefix>-<environment>-<instance>."
  fi

  [[ -n "$location" ]] || fatal "Location is required to create the isolated VM resource group. Pass --location or ensure the main resource group location can be resolved."
else
  vm_resource_group="$resource_group"
fi

if [[ -z "$vm_name" ]]; then
  fatal "Provide --vm-name"
fi

if [[ -z "$subnet_id" ]]; then
  subnet_id=$(tf_get "$tf_output" "vm_subnet.value.id")
  if [[ -z "$subnet_id" && -n "$tfvars_file" ]]; then
    [[ "$tfvars_should_create_vm_subnet" == "true" ]] || fatal "Subnet ID not provided and tfvars-derived configuration does not enable should_create_vm_subnet."
    [[ -n "$tfvars_vnet_name" && -n "$tfvars_vm_subnet_name" ]] || fatal "Subnet ID not provided and tfvars file is missing resource_prefix or environment needed to derive subnet names."
    info "Resolving VM subnet ID from Azure using tfvars-derived names..."
    subnet_id=$(az network vnet subnet show \
      --resource-group "$resource_group" \
      --vnet-name "$tfvars_vnet_name" \
      --name "$tfvars_vm_subnet_name" \
      --query id \
      --output tsv)
  fi
  [[ -n "$subnet_id" ]] || fatal "Subnet ID not provided and vm_subnet output not found. Enable should_create_vm_subnet in Terraform or pass --subnet-id."
fi

if [[ -z "$nsg_id" ]]; then
  nsg_id=$(tf_get "$tf_output" "network_security_group.value.id")
  if [[ -z "$nsg_id" && -n "$tfvars_file" ]]; then
    [[ -n "$tfvars_nsg_name" ]] || fatal "NSG ID not provided and tfvars file is missing resource_prefix or environment needed to derive the NSG name."
    info "Resolving network security group ID from Azure using tfvars-derived names..."
    nsg_id=$(az network nsg show \
      --resource-group "$resource_group" \
      --name "$tfvars_nsg_name" \
      --query id \
      --output tsv)
  fi
  [[ -n "$nsg_id" ]] || fatal "NSG ID not provided and network_security_group output not found. Apply Terraform outputs or pass --nsg-id."
fi

if [[ -z "$admin_password" && "$config_preview" != "true" ]]; then
  if [[ -t 0 ]]; then
    read -r -s -p "Admin password: " admin_password
    echo
  else
    fatal "Admin password not provided. Use --admin-password or ISAAC_LAB_VM_ADMIN_PASSWORD."
  fi
fi

vm_priority="Regular"
if [[ "$use_spot_vm" == "true" ]]; then
  vm_priority="Spot"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Deployment" "$deployment_name"
  print_kv "TFVars File" "${tfvars_file:-none}"
  print_kv "Resource Group" "$resource_group"
  print_kv "VM Resource Group" "$vm_resource_group"
  print_kv "Isolated VM RG" "$isolated_vm_rg"
  print_kv "Location" "${location:-resource-group default}"
  print_kv "Marketplace Terms" "$install_marketplace_requirements"
  print_kv "VM Name" "$vm_name"
  print_kv "Subnet ID" "$subnet_id"
  print_kv "NSG ID" "$nsg_id"
  print_kv "VM Size" "$vm_size"
  print_kv "Encryption At Host" "$should_enable_encryption_at_host"
  print_kv "VM Priority" "$vm_priority"
  if [[ "$use_spot_vm" == "true" ]]; then
    print_kv "Spot Eviction Policy" "$spot_eviction_policy"
  fi
  print_kv "Admin User" "$admin_username"
  print_kv "MDE Linux" "$enable_mde_linux"
  print_kv "Template" "$template_file"
  exit 0
fi

#------------------------------------------------------------------------------
# Deploy Isaac Sim VM
#------------------------------------------------------------------------------
section "Deploy Isaac Sim VM"

vm_resource_group_args=(
  group create
  --name "$vm_resource_group"
  --location "$location"
)

marketplace_args=(
  vm image terms accept
  --publisher "$marketplace_publisher"
  --offer "$marketplace_offer"
  --plan "$marketplace_plan"
)

deployment_args=(
  deployment group create
  --name "$deployment_name"
  --resource-group "$resource_group"
  --template-file "$template_file"
  --parameters "vmName=$vm_name"
  --parameters "subnetId=$subnet_id"
  --parameters "nsgId=$nsg_id"
  --parameters "adminUsername=$admin_username"
  --parameters "adminPassword=$admin_password"
  --parameters "vmSize=$vm_size"
  --parameters "shouldEnableEncryptionAtHost=$should_enable_encryption_at_host"
)

if [[ -n "$location" ]]; then
  deployment_args+=(--parameters "location=$location")
fi

if [[ "$isolated_vm_rg" == "true" ]]; then
  deployment_args+=(--parameters "vmResourceGroup=$vm_resource_group")
fi

if [[ "$enable_mde_linux" == "true" ]]; then
  deployment_args+=(--parameters 'mdeLinux={}')
fi

if [[ "$use_spot_vm" == "true" ]]; then
  deployment_args+=(--parameters "vmPriority=Spot")
  deployment_args+=(--parameters "spotEvictionPolicy=$spot_eviction_policy")
fi

if [[ "$isolated_vm_rg" == "true" ]]; then
  az "${vm_resource_group_args[@]}" >/dev/null
fi

if [[ "$install_marketplace_requirements" == "true" ]]; then
  az "${marketplace_args[@]}" >/dev/null
fi

az "${deployment_args[@]}"

#------------------------------------------------------------------------------
# Deployment Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Deployment" "$deployment_name"
print_kv "Resource Group" "$resource_group"
print_kv "VM Resource Group" "$vm_resource_group"
print_kv "Isolated VM RG" "$isolated_vm_rg"
print_kv "Location" "${location:-resource-group default}"
print_kv "Marketplace Terms" "$install_marketplace_requirements"
print_kv "VM Name" "$vm_name"
print_kv "Subnet ID" "$subnet_id"
print_kv "NSG ID" "$nsg_id"
print_kv "VM Size" "$vm_size"
print_kv "Encryption At Host" "$should_enable_encryption_at_host"
print_kv "VM Priority" "$vm_priority"
if [[ "$use_spot_vm" == "true" ]]; then
  print_kv "Spot Eviction Policy" "$spot_eviction_policy"
fi
print_kv "Admin User" "$admin_username"
print_kv "MDE Linux" "$enable_mde_linux"
info "Isaac Sim VM deployment complete"
