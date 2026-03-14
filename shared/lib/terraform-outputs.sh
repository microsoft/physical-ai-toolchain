#!/usr/bin/env bash
# Shared library for reading Terraform outputs from deploy/001-iac
# Sources this file and call read_terraform_outputs() to populate TF_OUTPUT

TERRAFORM_DIR="${TERRAFORM_DIR:-$(dirname "${BASH_SOURCE[0]}")/../../deploy/001-iac}"

# Read Terraform outputs from the specified directory into TF_OUTPUT
# Returns 1 if Terraform state cannot be read (non-fatal for scripts)
read_terraform_outputs() {
  local terraform_dir="${1:-$TERRAFORM_DIR}"

  if [[ ! -d "${terraform_dir}" ]]; then
    return 1
  fi

  if [[ ! -f "${terraform_dir}/terraform.tfstate" ]] && [[ ! -d "${terraform_dir}/.terraform" ]]; then
    return 1
  fi

  if ! TF_OUTPUT=$(cd "${terraform_dir}" && terraform output -json 2>/dev/null); then
    return 1
  fi

  export TF_OUTPUT
}

# Get a value from Terraform outputs using jq path
# Usage: get_output '.resource_group.value.name' 'default-value'
get_output() {
  local path="$1"
  local default="${2:-}"

  if [[ -z "${TF_OUTPUT:-}" ]]; then
    echo "${default}"
    return
  fi

  local value
  value=$(echo "${TF_OUTPUT}" | jq -r "${path} // empty")

  if [[ -z "${value}" ]]; then
    echo "${default}"
  else
    echo "${value}"
  fi
}

# Convenience function: get current Azure subscription ID
get_subscription_id() {
  az account show --query id -o tsv 2>/dev/null || echo ""
}

# Convenience function: get resource group name from Terraform outputs
get_resource_group() {
  get_output '.resource_group.value.name'
}

# Convenience function: get AKS cluster name from Terraform outputs
get_aks_cluster_name() {
  get_output '.aks_cluster.value.name'
}

# Convenience function: get AKS cluster ID from Terraform outputs
get_aks_cluster_id() {
  get_output '.aks_cluster.value.id'
}

# Convenience function: get AzureML workspace name from Terraform outputs
get_azureml_workspace() {
  get_output '.azureml_workspace.value.name'
}

# Convenience function: get ML workload identity ID from Terraform outputs
get_ml_identity_id() {
  get_output '.ml_workload_identity.value.id'
}

# Convenience function: get Key Vault name from Terraform outputs
get_key_vault_name() {
  get_output '.key_vault_name.value'
}

# Convenience function: get container registry name from Terraform outputs
get_container_registry() {
  get_output '.container_registry.value.name'
}

# Convenience function: get storage account name from Terraform outputs
get_storage_account() {
  get_output '.storage_account.value.name'
}

# Convenience function: derive compute target name from AKS cluster name
# Format: k8s-{first-12-chars-of-suffix}
get_compute_target() {
  local aks_name
  aks_name=$(get_aks_cluster_name)
  if [[ -n "${aks_name}" ]]; then
    local suffix="${aks_name#aks-}"
    suffix="${suffix:0:12}"
    echo "k8s-${suffix}"
  fi
}

# Convenience function: get PostgreSQL FQDN from Terraform outputs
get_postgresql_fqdn() {
  get_output '.postgresql_connection_info.value.fqdn'
}

# Convenience function: get PostgreSQL admin username from Terraform outputs
get_postgresql_admin() {
  get_output '.postgresql_connection_info.value.admin_username'
}

# Convenience function: get Redis hostname from Terraform outputs
get_redis_hostname() {
  get_output '.managed_redis_connection_info.value.hostname'
}

# Convenience function: get Redis port from Terraform outputs
get_redis_port() {
  get_output '.managed_redis_connection_info.value.port'
}
