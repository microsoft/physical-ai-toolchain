#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Deploy or undeploy the complete Physical AI E2E test infrastructure.
# cspell:ignore machinelearningservices softdeleted tfvar timespec undeployment
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null ||
    (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$DEFAULT_REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=lib/operation-state.sh
source "$SCRIPT_DIR/lib/operation-state.sh"
# shellcheck source=lib/terraform-operations.sh
source "$SCRIPT_DIR/lib/terraform-operations.sh"

DUMMY_HUGGINGFACE_TOKEN="invalid-e2e-token"
TFVARS_TEMPLATE="$SCRIPT_DIR/../templates/terraform.tfvars.example"
STORAGE_ACCOUNT_MAX_LENGTH=24
DATA_LAKE_NAME_PREFIX="stdl"
INSTANCE_LENGTH=3

show_help() {
    cat <<EOF
Usage: $(basename "$0") deploy|undeploy [OPTIONS]

Deploy or undeploy Terraform infrastructure and the ordered robotics, Azure ML,
and OSMO platform setup.

OPTIONS:
    --repo-root DIR         Repository checkout (default: $DEFAULT_REPO_ROOT)
    --max-attempts COUNT    Terraform apply/destroy attempts (default: 3)
    --resource-owner NAME   Lowercase alphanumeric resource-name owner
                            (default: signed-in Azure username)
    --confirm-resource-group NAME
                            Exact resource group required for undeploy
    --config-preview        Print resolved configuration without changing it
    -h, --help              Show this help message

The Azure CLI must already be authenticated. On an Azure Automation Hybrid
Runbook Worker, authenticate the worker's managed identity before invoking this
script:

    az login --identity

EXAMPLES:
    $(basename "$0") deploy
    $(basename "$0") undeploy
EOF
}

lowercase() {
    tr '[:upper:]' '[:lower:]'
}

action=""
repo_root="$DEFAULT_REPO_ROOT"
max_attempts=3
resource_owner="${E2E_RESOURCE_OWNER:-}"
confirm_resource_group=""
config_preview=false

[[ $# -gt 0 ]] || {
    show_help
    exit 1
}

case "$1" in
deploy | undeploy)
    action="$1"
    shift
    ;;
-h | --help)
    show_help
    exit 0
    ;;
*)
    fatal "First argument must be 'deploy' or 'undeploy'"
    ;;
esac

while [[ $# -gt 0 ]]; do
    case "$1" in
    --repo-root)
        [[ $# -ge 2 ]] || fatal "--repo-root requires a value"
        repo_root="$2"
        shift 2
        ;;
    --max-attempts)
        [[ $# -ge 2 ]] || fatal "--max-attempts requires a value"
        max_attempts="$2"
        shift 2
        ;;
    --resource-owner)
        [[ $# -ge 2 ]] || fatal "--resource-owner requires a value"
        resource_owner="$2"
        shift 2
        ;;
    --confirm-resource-group)
        [[ $# -ge 2 ]] || fatal "--confirm-resource-group requires a value"
        confirm_resource_group="$2"
        shift 2
        ;;
    --config-preview)
        config_preview=true
        shift
        ;;
    -h | --help)
        show_help
        exit 0
        ;;
    *)
        fatal "Unknown option: $1"
        ;;
    esac
done

[[ "$max_attempts" =~ ^[1-9][0-9]*$ ]] || fatal "--max-attempts must be a positive integer"

tf_dir="$repo_root/infrastructure/terraform"
tfvars="$tf_dir/terraform.tfvars"
setup_dir="$repo_root/infrastructure/setup"
generated_dir="$setup_dir/generated"
az_init="$tf_dir/prerequisites/az-sub-init.sh"

[[ -e "$repo_root/.git" ]] || fatal "Repository checkout not found: $repo_root"
[[ -f "$az_init" ]] || fatal "Azure subscription initializer not found: $az_init"
[[ -f "$TFVARS_TEMPLATE" ]] || fatal "Terraform variables template not found: $TFVARS_TEMPLATE"

require_tools az jq sha256sum terraform
if [[ "$action" == "deploy" ]]; then
    require_tools base64 curl envsubst git helm kubectl openssl osmo python3
fi

az account get-access-token --output none 2>/dev/null ||
    fatal "Azure CLI is not authenticated; run 'az login --identity' on the Hybrid Runbook Worker"

# shellcheck source=/dev/null
source "$az_init"

lock_dir=""
if command -v flock >/dev/null 2>&1; then
    exec 9>"$tf_dir/.e2e-infrastructure.lock"
    flock -n 9 || fatal "Another E2E infrastructure operation is already using $tf_dir"
else
    lock_dir="$tf_dir/.e2e-infrastructure.lock.d"
    mkdir "$lock_dir" 2>/dev/null || fatal "Another E2E infrastructure operation is already using $tf_dir"
fi

instance_backup=""
generated_tfvars=false
configured_tags_json=""
osmo_tunnel_pid=""
osmo_tunnel_log=""
cleanup() {
    local exit_code="$?"
    set +o errexit
    finalize_operation_evidence "$exit_code"
    if [[ -n "$osmo_tunnel_pid" ]] && kill -0 "$osmo_tunnel_pid" 2>/dev/null; then
        kill "$osmo_tunnel_pid"
        wait "$osmo_tunnel_pid" 2>/dev/null || true
    fi
    [[ -z "$osmo_tunnel_log" ]] || rm -f "$osmo_tunnel_log"
    rm -f "${tfvars}.tmp"
    [[ -z "$instance_backup" ]] || rm -f "$instance_backup"
    if [[ "$generated_tfvars" == "true" && "$config_preview" == "true" ]]; then
        rm -f "$tfvars"
    fi
    [[ -z "$lock_dir" ]] || rmdir "$lock_dir"
    return "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

read_tfvar() {
    local name="$1"
    local matches value

    matches=$(grep -Ec "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"[^\"]+\"[[:space:]]*$" "$tfvars" || true)
    [[ "$matches" -eq 1 ]] || fatal "Expected exactly one quoted '$name' assignment in $tfvars"
    value=$(sed -nE "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"([^\"]+)\"[[:space:]]*$/\\1/p" "$tfvars")
    printf '%s\n' "$value"
}

read_optional_tfvar() {
    local name="$1"
    local matches value

    matches=$(grep -Ec "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"[^\"]*\"[[:space:]]*$" "$tfvars" || true)
    [[ "$matches" -eq 1 ]] || fatal "Expected exactly one quoted '$name' assignment in $tfvars"
    value=$(sed -nE "s/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"([^\"]*)\"[[:space:]]*$/\\1/p" "$tfvars")
    printf '%s\n' "$value"
}

has_tfvar() {
    local name="$1"

    [[ "$(grep -Ec "^[[:space:]]*${name}[[:space:]]*=" "$tfvars" || true)" -gt 0 ]]
}

set_quoted_tfvar() {
    local name="$1" value="$2"
    local before_file before_hash after_hash

    before_file=$(mktemp)
    instance_backup="$before_file"
    cp "$tfvars" "$before_file"
    [[ "$(grep -Ec "^[[:space:]]*${name}[[:space:]]*=" "$tfvars")" -eq 1 ]] ||
        fatal "Expected exactly one '$name' assignment in $tfvars"
    sed -E "s/^([[:space:]]*${name}[[:space:]]*=[[:space:]]*)\"[^\"]*\"([[:space:]]*)$/\\1\"${value}\"\\2/" \
        "$tfvars" >"${tfvars}.tmp"
    [[ "$(grep -Ec "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"${value}\"[[:space:]]*$" "${tfvars}.tmp")" -eq 1 ]] || {
        rm -f "${tfvars}.tmp" "$before_file"
        fatal "Failed to update the $name assignment"
    }
    after_hash=$(sed -E "s/^([[:space:]]*${name}[[:space:]]*=[[:space:]]*)\"[^\"]*\"([[:space:]]*)$/\\1\"VALUE\"\\2/" \
        "${tfvars}.tmp" | sha256sum | awk '{print $1}')
    before_hash=$(sed -E "s/^([[:space:]]*${name}[[:space:]]*=[[:space:]]*)\"[^\"]*\"([[:space:]]*)$/\\1\"VALUE\"\\2/" \
        "$before_file" | sha256sum | awk '{print $1}')
    if [[ "$before_hash" != "$after_hash" ]]; then
        rm -f "${tfvars}.tmp" "$before_file"
        fatal "Refusing tfvars update because content other than $name changed"
    fi
    mv "${tfvars}.tmp" "$tfvars"
    rm -f "$before_file"
    instance_backup=""
}

remove_quoted_tfvar() {
    local name="$1"
    local matches

    matches=$(grep -Ec "^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"[^\"]*\"[[:space:]]*$" "$tfvars" || true)
    [[ "$matches" -eq 1 ]] || fatal "Expected exactly one quoted '$name' assignment in $tfvars"
    sed -E "/^[[:space:]]*${name}[[:space:]]*=[[:space:]]*\"[^\"]*\"[[:space:]]*$/d" \
        "$tfvars" >"${tfvars}.tmp"
    mv "${tfvars}.tmp" "$tfvars"
}

normalize_resource_owner() {
    local owner="$1"
    local normalized

    normalized=$(printf '%s' "$owner" | lowercase | tr -cd 'a-z0-9')
    [[ -n "$normalized" ]] ||
        fatal "The resource owner has no usable letters or digits; pass --resource-owner"
    printf '%s\n' "$normalized"
}

resolve_resource_owner() {
    local base_prefix="$1"
    local azure_identity normalized max_owner_length

    if [[ -z "$resource_owner" ]]; then
        azure_identity=$(az account show --query user.name --output tsv --only-show-errors)
        resource_owner="${azure_identity%%@*}"
    fi
    normalized=$(normalize_resource_owner "$resource_owner")
    max_owner_length=$((STORAGE_ACCOUNT_MAX_LENGTH - ${#DATA_LAKE_NAME_PREFIX} - ${#base_prefix} - ${#environment} - INSTANCE_LENGTH))
    [[ "$max_owner_length" -gt 0 ]] ||
        fatal "resource_prefix '$base_prefix' leaves no room for an owner in the data-lake storage-account name"
    if [[ ${#normalized} -gt "$max_owner_length" ]]; then
        warn "Truncating resource owner '$normalized' to $max_owner_length characters for Azure resource-name limits"
        normalized="${normalized:0:max_owner_length}"
    fi
    resource_owner="$normalized"
}

prepare_resource_prefix() {
    local persisted_owner

    if has_tfvar resource_owner; then
        persisted_owner=$(read_optional_tfvar resource_owner)
        if [[ "$state_count" -gt 0 || -z "$resource_owner" ]]; then
            resource_owner="$persisted_owner"
        fi
        resolve_resource_owner "$resource_prefix"
        resource_prefix="${resource_prefix}${resource_owner}"
        if [[ "$config_preview" == "false" ]]; then
            set_quoted_tfvar resource_prefix "$resource_prefix"
            remove_quoted_tfvar resource_owner
            info "Migrated resource ownership into resource_prefix"
        fi
        return
    fi

    if [[ "$generated_tfvars" == "true" ]]; then
        resolve_resource_owner "$resource_prefix"
        resource_prefix="${resource_prefix}${resource_owner}"
        set_quoted_tfvar resource_prefix "$resource_prefix"
        return
    fi

    [[ -z "$resource_owner" ]] ||
        fatal "--resource-owner applies only when the driver creates terraform.tfvars"
}

terraform_state_list() {
    local output

    if ! output=$(terraform -chdir="$tf_dir" state list 2>&1); then
        if [[ "$output" == *"No state file was found"* ]]; then
            return
        fi
        fatal "Terraform state could not be read: $output"
    fi
    printf '%s\n' "$output"
}

terraform_state_count() {
    local state

    state=$(terraform_state_list)
    if [[ -z "$state" ]]; then
        printf '0\n'
    else
        printf '%s\n' "$state" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '
    fi
}

load_configured_tags() {
    configured_tags_json=$(terraform -chdir="$tf_dir" console -var-file=terraform.tfvars <<<'jsonencode(var.tags)' | jq -er '.')
}

create_resource_group() {
    local resource_group

    resource_group="rg-${resource_prefix}-${environment}-$(read_tfvar instance)"
    az group create \
        --name "$resource_group" \
        --location "$location" \
        --output none \
        --only-show-errors
    info "Created resource group $resource_group"
    write_destroy_context "$resource_group"
    purge_soft_deleted_resources "$resource_group"
}

write_destroy_context() {
    local resource_group="$1"
    local destroy_context

    destroy_context="$generated_dir/$environment/e2e-destroy-context.json"
    mkdir -p "$(dirname "$destroy_context")"
    jq -n \
        --arg environment "$environment" \
        --arg instance "$(read_tfvar instance)" \
        --arg location "$location" \
        --arg resource_group_id "/subscriptions/${ARM_SUBSCRIPTION_ID}/resourceGroups/${resource_group}" \
        '{
            environment: $environment,
            instance: $instance,
            location: $location,
            keyVaultName: "",
            resourceGroupId: $resource_group_id,
            aksId: ""
        }' >"$destroy_context"
    chmod 600 "$destroy_context"
}

purge_soft_deleted_resources() {
    local resource_group="$1"
    local resources resource name type type_lower deleted_vaults vault_name vault_location
    local resource_group_lower

    resources=$(az resource list \
        --resource-group "$resource_group" \
        --output json \
        --only-show-errors)
    while IFS= read -r resource; do
        name=$(jq -er '.name' <<<"$resource")
        type=$(jq -er '.type' <<<"$resource")
        type_lower=$(printf '%s' "$type" | lowercase)
        case "$type_lower" in
        microsoft.machinelearningservices/workspaces)
            az ml workspace delete \
                --name "$name" \
                --resource-group "$resource_group" \
                --permanently-delete \
                --yes \
                --only-show-errors
            info "Purged soft-deleted Azure ML workspace $name"
            ;;
        *)
            fatal "Cannot purge unsupported soft-deleted resource type '$type' for '$name'"
            ;;
        esac
    done < <(
        jq -c '.[] | select((.properties.provisioningState // "" | ascii_downcase) == "softdeleted")' \
            <<<"$resources"
    )

    deleted_vaults=$(az keyvault list-deleted --output json --only-show-errors)
    resource_group_lower=$(printf '%s' "$resource_group" | lowercase)
    while IFS= read -r resource; do
        vault_name=$(jq -er '.name' <<<"$resource")
        vault_location=$(jq -er '.properties.location' <<<"$resource")
        az keyvault purge \
            --name "$vault_name" \
            --location "$vault_location" \
            --only-show-errors
        info "Purged soft-deleted Key Vault $vault_name"
    done < <(
        jq -c \
            --arg resource_group_segment "/resourcegroups/${resource_group_lower}/" \
            '.[] | select((.properties.vaultId // "" | ascii_downcase) | contains($resource_group_segment))' \
            <<<"$deleted_vaults"
    )
}

configure_resource_group() {
    local resource_group resource_group_id entry tag_key tag_value
    local tag_args=()

    resource_group="rg-${resource_prefix}-${environment}-$(read_tfvar instance)"
    resource_group_id=$(az group show \
        --name "$resource_group" \
        --query id \
        --output tsv \
        --only-show-errors)
    [[ -n "$resource_group_id" ]] || fatal "Azure did not return an ID for resource group $resource_group"

    while IFS= read -r entry; do
        tag_key=$(jq -er '.key' <<<"$entry")
        tag_value=$(jq -er '.value' <<<"$entry")
        tag_args+=("${tag_key}=${tag_value}")
    done < <(jq -c 'to_entries[]' <<<"$configured_tags_json")

    if [[ ${#tag_args[@]} -gt 0 ]]; then
        az tag update \
            --resource-id "$resource_group_id" \
            --operation Merge \
            --tags "${tag_args[@]}" \
            --output none \
            --only-show-errors
    fi
}

ensure_tfvars() {
    local state_count outputs state_resource_group state_instance

    [[ -f "$tfvars" ]] && return

    cp "$TFVARS_TEMPLATE" "${tfvars}.tmp"
    mv "${tfvars}.tmp" "$tfvars"
    generated_tfvars=true
    info "Generated $tfvars from the E2E infrastructure template"

    terraform -chdir="$tf_dir" init -input=false
    state_count=$(terraform_state_count)
    [[ "$state_count" -gt 0 ]] || return 0

    outputs=$(terraform -chdir="$tf_dir" output -json 2>/dev/null || true)
    state_resource_group=$(jq -r '.resource_group.value.name // empty' <<<"$outputs")
    [[ "$state_resource_group" =~ -([0-9]{3})$ ]] ||
        fatal "Terraform state is non-empty, but its instance cannot be recovered from the resource-group output"
    state_instance="${BASH_REMATCH[1]}"
    set_instance "$state_instance"
}

key_vault_name_available() {
    local name="$1"
    local body

    body=$(jq -cn --arg name "$name" '{name: $name, type: "Microsoft.KeyVault/vaults"}')
    az rest \
        --method post \
        --url "https://management.azure.com/subscriptions/${ARM_SUBSCRIPTION_ID}/providers/Microsoft.KeyVault/checkNameAvailability?api-version=2023-07-01" \
        --body "$body" \
        --query nameAvailable \
        --output tsv \
        --only-show-errors
}

candidate_available() {
    local candidate="$1"
    local resource_group key_vault storage data_lake registry
    local result

    resource_group="rg-${resource_prefix}-${environment}-${candidate}"
    key_vault="kv${resource_prefix}${environment}${candidate}"
    storage="st${resource_prefix}${environment}${candidate}"
    data_lake="stdl${resource_prefix}${environment}${candidate}"
    registry="acr${resource_prefix}${environment}${candidate}"

    if ! result=$(az group exists --name "$resource_group" --output tsv --only-show-errors 2>&1); then
        fatal "Resource-group availability check failed: $result"
    fi
    [[ "$result" == "false" ]] || return 1

    if ! result=$(key_vault_name_available "$key_vault" 2>&1); then
        fatal "Key Vault availability check failed: $result"
    fi
    [[ "$result" == "true" ]] || return 1

    if ! result=$(az storage account check-name --name "$storage" --query nameAvailable --output tsv --only-show-errors 2>&1); then
        fatal "Storage-account availability check failed: $result"
    fi
    [[ "$result" == "true" ]] || return 1

    if ! result=$(az storage account check-name --name "$data_lake" --query nameAvailable --output tsv --only-show-errors 2>&1); then
        fatal "Data-lake availability check failed: $result"
    fi
    [[ "$result" == "true" ]] || return 1

    if ! result=$(az acr check-name --name "$registry" --query nameAvailable --output tsv --only-show-errors 2>&1); then
        fatal "Container-registry availability check failed: $result"
    fi
    [[ "$result" == "true" ]] || return 1
}

select_instance() {
    local number candidate

    validate_generated_names
    for number in $(seq 1 999); do
        printf -v candidate '%03d' "$number"
        if candidate_available "$candidate"; then
            printf '%s\n' "$candidate"
            return
        fi
    done

    fatal "No globally available instance found in the range 001-999"
}

validate_generated_names() {
    local key_vault storage data_lake registry

    key_vault="kv${resource_prefix}${environment}001"
    storage="st${resource_prefix}${environment}001"
    data_lake="stdl${resource_prefix}${environment}001"
    registry="acr${resource_prefix}${environment}001"

    [[ "$key_vault" =~ ^[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]$ ]] ||
        fatal "Generated Key Vault name is invalid: $key_vault"
    [[ "$storage" =~ ^[a-z0-9]{3,24}$ ]] ||
        fatal "Generated storage-account name is invalid: $storage"
    [[ "$data_lake" =~ ^[a-z0-9]{3,24}$ ]] ||
        fatal "Generated data-lake name is invalid: $data_lake"
    [[ "$registry" =~ ^[A-Za-z0-9]{5,50}$ ]] ||
        fatal "Generated container-registry name is invalid: $registry"
}

set_instance() {
    set_quoted_tfvar instance "$1"
}

reconcile_failed_managed_redis() {
    local resource_address resource_group redis_name resource_id provisioning_state
    local state_list attempt

    resource_address="module.platform.azurerm_managed_redis.main[0]"
    resource_group="rg-${resource_prefix}-${environment}-$(read_tfvar instance)"
    redis_name="redis-${resource_prefix}-${environment}-$(read_tfvar instance)"

    if ! resource_id=$(az resource list \
        --resource-group "$resource_group" \
        --resource-type Microsoft.Cache/redisEnterprise \
        --query "[?name=='${redis_name}'].id | [0]" \
        --output tsv \
        --only-show-errors 2>&1); then
        fatal "Managed Redis lookup failed: $resource_id"
    fi
    [[ -n "$resource_id" ]] || return 0

    if ! provisioning_state=$(az resource show \
        --ids "$resource_id" \
        --query properties.provisioningState \
        --output tsv \
        --only-show-errors 2>&1); then
        fatal "Managed Redis provisioning-state check failed: $provisioning_state"
    fi
    [[ "$provisioning_state" == "Failed" ]] || return 0

    state_list=$(terraform_state_list)
    if grep -Fxq "$resource_address" <<<"$state_list"; then
        terraform -chdir="$tf_dir" state rm "$resource_address" >/dev/null
    fi

    warn "Deleting failed Managed Redis deployment before retrying"
    az resource delete --ids "$resource_id" --only-show-errors
    for attempt in $(seq 1 30); do
        if ! resource_id=$(az resource list \
            --resource-group "$resource_group" \
            --resource-type Microsoft.Cache/redisEnterprise \
            --query "[?name=='${redis_name}'].id | [0]" \
            --output tsv \
            --only-show-errors 2>&1); then
            fatal "Managed Redis deletion check failed: $resource_id"
        fi
        if [[ -z "$resource_id" ]]; then
            info "Removed failed Managed Redis deployment"
            return
        fi
        [[ "$attempt" -lt 30 ]] || fatal "Failed Managed Redis deployment was not deleted within 5 minutes"
        sleep 10
    done
}

run_terraform_command() {
    local command="$1"
    shift
    terraform -chdir="$tf_dir" "$command" "$@"
}

forget_undeletable_key_vault_secrets() {
    local resource_address
    local resource_addresses=()

    while IFS= read -r resource_address; do
        [[ -z "$resource_address" ]] || resource_addresses+=("$resource_address")
    done < <(
        terraform -chdir="$tf_dir" show -json |
            jq -r '
                .. | objects |
                select(
                    .address? and
                    .type? == "azapi_resource" and
                    ((.values.type? // "") | startswith("Microsoft.KeyVault/vaults/secrets@"))
                ) |
                .address
            '
    )

    for resource_address in "${resource_addresses[@]+${resource_addresses[@]}}"; do
        terraform -chdir="$tf_dir" state rm "$resource_address" >/dev/null
        info "Removed undeletable ARM-plane Key Vault secret from Terraform state: $resource_address"
    done
}

forget_prevent_destroy_resources() {
    local attempt plan_file resource_address
    local resource_addresses=()

    plan_file=$(mktemp "${TMPDIR:-/tmp}/e2e-destroy-plan.XXXXXX")
    for attempt in $(seq 1 10); do
        if run_terraform_command plan -destroy -input=false -var-file=terraform.tfvars -json >"$plan_file"; then
            rm -f "$plan_file"
            return
        fi

        resource_addresses=()
        while IFS= read -r resource_address; do
            [[ -z "$resource_address" ]] || resource_addresses+=("$resource_address")
        done < <(
            extract_prevent_destroy_addresses "$plan_file"
        )

        if [[ ${#resource_addresses[@]} -eq 0 ]]; then
            jq -r '
                select(.type == "diagnostic") |
                "\(.diagnostic.severity): \(.diagnostic.summary)\n\(.diagnostic.detail)"
            ' "$plan_file" >&2
            rm -f "$plan_file"
            fatal "Terraform destroy preflight failed without a prevent_destroy diagnostic"
        fi

        for resource_address in "${resource_addresses[@]+${resource_addresses[@]}}"; do
            terraform -chdir="$tf_dir" state rm "$resource_address" >/dev/null
            info "Removed E2E prevent_destroy resource from Terraform state: $resource_address"
        done
    done

    rm -f "$plan_file"
    fatal "Terraform destroy preflight still found prevent_destroy resources after 10 attempts"
}

terraform_operation_attempt() {
    local operation="$1"

    if [[ "$operation" == "plan" ]]; then
        run_terraform_command plan -input=false -var-file=terraform.tfvars
    else
        run_terraform_command "$operation" -auto-approve -input=false -var-file=terraform.tfvars
    fi
}

verify_resume_instance() {
    local outputs actual_resource_group expected_resource_group

    outputs=$(terraform -chdir="$tf_dir" output -json 2>/dev/null || true)
    actual_resource_group=$(jq -r '.resource_group.value.name // empty' <<<"$outputs")
    if [[ -z "$actual_resource_group" ]]; then
        return
    fi

    expected_resource_group="rg-${resource_prefix}-${environment}-$(read_tfvar instance)"
    [[ "$actual_resource_group" == "$expected_resource_group" ]] ||
        fatal "Terraform state belongs to '$actual_resource_group', but terraform.tfvars resolves '$expected_resource_group'"
}

verify_deployment_outputs() {
    local outputs resource_group_id aks_id live_resource_group live_resource_group_id live_aks_id
    local normalized_resource_group_id normalized_live_resource_group_id
    local normalized_aks_id normalized_live_aks_id
    local required

    outputs=$(terraform -chdir="$tf_dir" output -json)
    for required in resource_group aks_cluster azureml_workspace key_vault_name storage_account node_pools osmo_workload_identity; do
        jq -e --arg name "$required" '.[$name].value != null' <<<"$outputs" >/dev/null ||
            fatal "Required Terraform output is null or missing: $required"
    done

    resource_group_id=$(jq -r '.resource_group.value.id' <<<"$outputs")
    aks_id=$(jq -r '.aks_cluster.value.id' <<<"$outputs")
    live_resource_group=$(az group show --name "${resource_group_id##*/}" --output json --only-show-errors)
    live_resource_group_id=$(jq -r '.id' <<<"$live_resource_group")
    live_aks_id=$(az resource show --ids "$aks_id" --query id --output tsv --only-show-errors)

    normalized_resource_group_id=$(printf '%s' "$resource_group_id" | lowercase)
    normalized_live_resource_group_id=$(printf '%s' "$live_resource_group_id" | lowercase)
    normalized_aks_id=$(printf '%s' "$aks_id" | lowercase)
    normalized_live_aks_id=$(printf '%s' "$live_aks_id" | lowercase)

    [[ "$normalized_resource_group_id" == "$normalized_live_resource_group_id" ]] ||
        fatal "Terraform resource group does not match Azure"
    [[ "$normalized_aks_id" == "$normalized_live_aks_id" ]] || fatal "Terraform AKS cluster does not match Azure"
}

select_osmo_private_ip() {
    local outputs resource_group aks_cluster subnet_id subnet subnet_prefix vnet_name probe availability
    local kubeconfig existing_ip

    outputs=$(terraform -chdir="$tf_dir" output -json)
    resource_group=$(jq -er '.resource_group.value.name' <<<"$outputs")
    aks_cluster=$(jq -er '.aks_cluster.value.name' <<<"$outputs")
    kubeconfig="$HOME/.kube/physical-ai-toolchain/${aks_cluster}.yaml"
    if [[ -f "$kubeconfig" ]]; then
        existing_ip=$(KUBECONFIG="$kubeconfig" kubectl get svc azureml-ingress-nginx-internal-lb \
            --namespace azureml \
            -o jsonpath='{.metadata.annotations.service\.beta\.kubernetes\.io/azure-load-balancer-ipv4}' \
            2>/dev/null || true)
        if [[ -n "$existing_ip" ]]; then
            printf '%s\n' "$existing_ip"
            return
        fi
    fi

    # shellcheck disable=SC2016
    subnet_id=$(az aks show \
        --resource-group "$resource_group" \
        --name "$aks_cluster" \
        --query 'agentPoolProfiles[?mode==`System`] | [0].vnetSubnetId' \
        --output tsv \
        --only-show-errors)
    [[ -n "$subnet_id" ]] || fatal "AKS system subnet could not be resolved"

    subnet=$(az network vnet subnet show --ids "$subnet_id" --output json --only-show-errors)
    subnet_prefix=$(jq -r '.addressPrefix // .addressPrefixes[0] // empty' <<<"$subnet")
    vnet_name=$(awk -F/ '{for (i = 1; i <= NF; i++) if ($i == "virtualNetworks") print $(i + 1)}' <<<"$subnet_id")
    [[ -n "$subnet_prefix" && -n "$vnet_name" ]] || fatal "AKS subnet address range could not be resolved"

    while IFS= read -r probe; do
        if ! availability=$(az network vnet check-ip-address \
            --resource-group "$resource_group" \
            --name "$vnet_name" \
            --ip-address "$probe" \
            --output json \
            --only-show-errors 2>&1); then
            fatal "Private IP availability check failed: $availability"
        fi
        if [[ "$(jq -r '.available' <<<"$availability")" == "true" ]]; then
            printf '%s\n' "$probe"
            return
        fi
    done < <(
        python3 - "$subnet_prefix" <<'PYTHON'
import ipaddress
import sys

network = ipaddress.ip_network(sys.argv[1])
for offset in range(2, min(network.num_addresses - 4, 34)):
    print(network.broadcast_address - offset)
PYTHON
    )

    fatal "Azure did not return an available private IP in $subnet_prefix"
}

verify_platform() {
    local osmo_private_ip="$1"
    local outputs resource_group aks_cluster aks_id workspace compute_name kubeconfig extension_name
    local kube_server live_aks_fqdn osmo_service_url osmo_credentials attempt

    outputs=$(terraform -chdir="$tf_dir" output -json)
    resource_group=$(jq -er '.resource_group.value.name' <<<"$outputs")
    aks_cluster=$(jq -er '.aks_cluster.value.name' <<<"$outputs")
    aks_id=$(jq -er '.aks_cluster.value.id' <<<"$outputs")
    workspace=$(jq -er '.azureml_workspace.value.name' <<<"$outputs")
    kubeconfig="$HOME/.kube/physical-ai-toolchain/${aks_cluster}.yaml"
    extension_name="azureml-${aks_cluster}"
    compute_name="k8s-${aks_cluster#aks-}"
    compute_name="${compute_name:0:16}"
    compute_name="${compute_name%-}"

    [[ -f "$kubeconfig" ]] || fatal "Expected isolated kubeconfig not found: $kubeconfig"
    kube_server=$(KUBECONFIG="$kubeconfig" kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
    live_aks_fqdn=$(az aks show \
        --resource-group "$resource_group" \
        --name "$aks_cluster" \
        --query fqdn \
        --output tsv \
        --only-show-errors)
    [[ "$kube_server" == "https://${live_aks_fqdn}:443" ]] || fatal "Kubeconfig does not target the Terraform-resolved AKS cluster"

    KUBECONFIG="$kubeconfig" helm status gpu-operator -n gpu-operator >/dev/null
    KUBECONFIG="$kubeconfig" helm status kai-scheduler -n kai-scheduler >/dev/null
    KUBECONFIG="$kubeconfig" helm status osmo -n osmo-control-plane >/dev/null
    KUBECONFIG="$kubeconfig" helm status osmo-operator -n osmo-operator >/dev/null

    [[ "$(az k8s-extension show \
        --cluster-type managedClusters \
        --cluster-name "$aks_cluster" \
        --resource-group "$resource_group" \
        --name "$extension_name" \
        --query provisioningState \
        --output tsv \
        --only-show-errors)" == "Succeeded" ]] || fatal "Azure ML Kubernetes extension is not ready"

    [[ "$(az ml compute show \
        --name "$compute_name" \
        --resource-group "$resource_group" \
        --workspace-name "$workspace" \
        --query provisioning_state \
        --output tsv \
        --only-show-errors)" == "Succeeded" ]] || fatal "Azure ML attached compute is not ready"

    KUBECONFIG="$kubeconfig" kubectl wait \
        --for=condition=Available \
        deployment \
        --all \
        --namespace osmo-control-plane \
        --timeout=10m >/dev/null
    KUBECONFIG="$kubeconfig" kubectl wait \
        --for=condition=Available \
        deployment \
        --all \
        --namespace osmo-operator \
        --timeout=10m >/dev/null

    osmo_service_url="http://${osmo_private_ip}"
    if ! curl --fail --silent --show-error --max-time 5 "${osmo_service_url}/api/version" >/dev/null; then
        osmo_service_url="http://127.0.0.1:9000"
        osmo_tunnel_log=$(mktemp)
        KUBECONFIG="$kubeconfig" kubectl port-forward \
            --namespace osmo-control-plane \
            service/osmo-gateway \
            9000:80 \
            >"$osmo_tunnel_log" 2>&1 &
        osmo_tunnel_pid=$!
        for attempt in $(seq 1 30); do
            if ! kill -0 "$osmo_tunnel_pid" 2>/dev/null; then
                fatal "OSMO gateway tunnel exited before becoming ready: $(<"$osmo_tunnel_log")"
            fi
            if curl --fail --silent --show-error --max-time 5 "${osmo_service_url}/api/version" >/dev/null; then
                break
            fi
            [[ "$attempt" -lt 30 ]] || fatal "OSMO gateway tunnel did not become ready"
            sleep 2
        done
        info "Connected to the OSMO private gateway through a temporary kubectl tunnel"
    fi
    osmo login "${osmo_service_url}/" --method dev --username admin >/dev/null
    osmo profile set pool default >/dev/null
    osmo_credentials=$(osmo credential --format-type json list)
    if ! jq -e 'any(.credentials[]?; .cred_name == "huggingface" and .cred_type == "GENERIC")' \
        <<<"$osmo_credentials" >/dev/null; then
        osmo credential set huggingface \
            --type GENERIC \
            --payload "hf_token=${DUMMY_HUGGINGFACE_TOKEN}" \
            >/dev/null
        osmo_credentials=$(osmo credential --format-type json list)
    fi
    jq -e 'any(.credentials[]?; .cred_name == "huggingface" and .cred_type == "GENERIC")' \
        <<<"$osmo_credentials" >/dev/null ||
        fatal "OSMO Hugging Face credential was not created"
    osmo workflow list --count 1 --format-type json >/dev/null
}

deploy() {
    local state_count selected_instance setup_script osmo_private_ip resource_group group_exists

    terraform -chdir="$tf_dir" init -input=false
    record_operation_event "deploy" "validating"
    state_count=$(terraform_state_count)
    load_configured_tags

    if [[ "$state_count" -eq 0 ]]; then
        selected_instance=$(select_instance)
        info "Selected unused Azure instance: $selected_instance"
        if [[ "$config_preview" == "true" ]]; then
            printf 'Action: deploy\nResource prefix: %s\nInstance: %s\nResource group: %s\nTerraform: %s\n' \
                "$resource_prefix" \
                "$selected_instance" \
                "rg-${resource_prefix}-${environment}-${selected_instance}" \
                "$tf_dir"
            return
        fi
        set_instance "$selected_instance"
        create_resource_group
    else
        selected_instance=$(read_tfvar instance)
        verify_resume_instance
        info "Resuming non-empty Terraform state with instance $selected_instance"
        if [[ "$config_preview" == "true" ]]; then
            printf 'Action: deploy (resume)\nInstance: %s\nTerraform resources: %s\n' \
                "$selected_instance" "$state_count"
            return
        fi
        resource_group="rg-${resource_prefix}-${environment}-${selected_instance}"
        if ! group_exists=$(az group exists --name "$resource_group" --output tsv --only-show-errors 2>&1); then
            fatal "Resource-group existence check failed: $group_exists"
        fi
        if [[ "$group_exists" == "false" ]]; then
            create_resource_group
        fi
    fi

    configure_resource_group
    reconcile_failed_managed_redis
    run_terraform_operation_with_retries plan
    run_terraform_operation_with_retries apply
    verify_deployment_outputs
    osmo_private_ip=$(select_osmo_private_ip)
    info "Selected OSMO private service IP: $osmo_private_ip"

    for setup_script in \
        "$setup_dir/01-deploy-robotics-charts.sh" \
        "$setup_dir/02-deploy-azureml-extension.sh" \
        "$setup_dir/03-deploy-osmo.sh"; do
        info "Previewing $(basename "$setup_script")"
        if [[ "$(basename "$setup_script")" == "03-deploy-osmo.sh" ]]; then
            (
                cd "$setup_dir"
                "./$(basename "$setup_script")" --private-service-ip "$osmo_private_ip" --config-preview
            )
        else
            (
                cd "$setup_dir"
                "./$(basename "$setup_script")" --config-preview
            )
        fi
        info "Running $(basename "$setup_script")"
        if [[ "$(basename "$setup_script")" == "03-deploy-osmo.sh" ]]; then
            (
                cd "$setup_dir"
                "./$(basename "$setup_script")" --private-service-ip "$osmo_private_ip"
            )
        else
            (
                cd "$setup_dir"
                "./$(basename "$setup_script")"
            )
        fi
    done

    verify_deployment_outputs
    verify_platform "$osmo_private_ip"
    record_operation_event "deploy" "completed" "instance $selected_instance"
    info "E2E infrastructure deployment completed for instance $selected_instance"
}

undeploy() {
    local state_count outputs environment_name instance location key_vault_name resource_group_id aks_id
    local destroy_context kubeconfig resource_group_name deleted_vault_count attempt
    local context_environment context_instance

    terraform -chdir="$tf_dir" init -input=false
    environment_name=$(read_tfvar environment)
    instance=$(read_tfvar instance)
    location=$(read_tfvar location)
    destroy_context="$generated_dir/$environment_name/e2e-destroy-context.json"
    state_count=$(terraform_state_count)

    if [[ "$state_count" -gt 0 ]]; then
        outputs=$(terraform -chdir="$tf_dir" output -json)
    elif [[ -f "$destroy_context" ]]; then
        outputs="{}"
        info "Terraform state is already empty; resuming cleanup from the captured destroy context"
    else
        fatal "Terraform state is empty and no destroy context exists; refusing to infer an environment to destroy"
    fi

    key_vault_name=$(jq -r '.key_vault_name.value // empty' <<<"$outputs")
    resource_group_id=$(jq -r '.resource_group.value.id // empty' <<<"$outputs")
    aks_id=$(jq -r '.aks_cluster.value.id // empty' <<<"$outputs")
    resource_group_name=$(jq -r '.resource_group.value.name // empty' <<<"$outputs")

    if [[ -f "$destroy_context" ]]; then
        context_environment=$(jq -er '.environment' "$destroy_context") ||
            fatal "Destroy context has no environment: $destroy_context"
        context_instance=$(jq -er '.instance' "$destroy_context") ||
            fatal "Destroy context has no instance: $destroy_context"
        [[ "$context_environment" == "$environment_name" ]] ||
            fatal "Destroy context environment '$context_environment' does not match '$environment_name'"
        [[ "$context_instance" == "$instance" ]] ||
            fatal "Destroy context instance '$context_instance' does not match '$instance'"

        [[ -n "$key_vault_name" ]] ||
            key_vault_name=$(jq -r '.keyVaultName // empty' "$destroy_context")
        [[ -n "$resource_group_id" ]] ||
            resource_group_id=$(jq -r '.resourceGroupId // empty' "$destroy_context")
        [[ -n "$aks_id" ]] ||
            aks_id=$(jq -r '.aksId // empty' "$destroy_context")
    fi
    if [[ -z "$resource_group_name" && -n "$resource_group_id" ]]; then
        resource_group_name="${resource_group_id##*/}"
    fi

    if [[ -n "$resource_group_name" ]] &&
        ! az group show --name "$resource_group_name" --output none --only-show-errors 2>/dev/null; then
        warn "Terraform-resolved resource group is already absent: $resource_group_name"
    fi
    if [[ -n "$aks_id" ]] &&
        ! az resource show --ids "$aks_id" --output none --only-show-errors 2>/dev/null; then
        warn "Terraform-resolved AKS cluster is already absent"
    fi

    if [[ "$config_preview" == "true" ]]; then
        printf 'Action: undeploy\nInstance: %s\nTerraform resources: %s\nResource group: %s\n' \
            "$instance" "$state_count" "$resource_group_id"
        return
    fi

    require_undeploy_confirmation "$resource_group_name"
    record_operation_event "undeploy" "started" "$resource_group_name"
    mkdir -p "$(dirname "$destroy_context")"
    jq -n \
        --arg environment "$environment_name" \
        --arg instance "$instance" \
        --arg location "$location" \
        --arg key_vault_name "$key_vault_name" \
        --arg resource_group_id "$resource_group_id" \
        --arg aks_id "$aks_id" \
        '{environment: $environment, instance: $instance, location: $location, keyVaultName: $key_vault_name, resourceGroupId: $resource_group_id, aksId: $aks_id}' \
        >"$destroy_context"

    if [[ "$state_count" -gt 0 ]]; then
        forget_undeletable_key_vault_secrets
        forget_prevent_destroy_resources
        run_terraform_operation_with_retries destroy
    fi
    [[ "$(terraform_state_count)" -eq 0 ]] || fatal "Terraform state is not empty after destroy"
    if [[ -n "$resource_group_name" ]]; then
        if [[ "$(az group exists --name "$resource_group_name" --output tsv --only-show-errors)" == "true" ]]; then
            purge_soft_deleted_resources "$resource_group_name"
            az group delete \
                --name "$resource_group_name" \
                --yes \
                --only-show-errors
            record_operation_event "resource-group-delete" "submitted" "$resource_group_name"
        fi
        [[ "$(az group exists --name "$resource_group_name" --output tsv --only-show-errors)" == "false" ]] ||
            fatal "Resource group still exists after deletion"
    fi

    if [[ -n "$key_vault_name" ]]; then
        deleted_vault_count=$(az keyvault list-deleted \
            --query "[?name=='${key_vault_name}'] | length(@)" \
            --output tsv \
            --only-show-errors)
        if [[ "$deleted_vault_count" -gt 0 ]]; then
            for attempt in $(seq 1 10); do
                if az keyvault purge --name "$key_vault_name" --location "$location" --only-show-errors; then
                    break
                fi
                [[ "$attempt" -lt 10 ]] || fatal "Key Vault purge failed after 10 attempts"
                sleep 30
            done

            for attempt in $(seq 1 20); do
                if [[ "$(key_vault_name_available "$key_vault_name")" == "true" ]]; then
                    break
                fi
                [[ "$attempt" -lt 20 ]] || fatal "Key Vault name did not become available after purge"
                sleep 15
            done
        else
            info "Key Vault is not soft-deleted; purge is not required"
        fi
    else
        warn "Key Vault output is unavailable; no Key Vault purge is required for the partial state"
    fi

    if [[ -n "$aks_id" ]]; then
        kubeconfig="$HOME/.kube/physical-ai-toolchain/$(basename "$aks_id").yaml"
        [[ ! -e "$kubeconfig" ]] || rm -f "$kubeconfig"
    fi
    rm -f "$destroy_context"

    record_operation_event "undeploy" "completed" "$resource_group_name"
    info "E2E infrastructure undeployment completed for instance $instance"
}

ensure_tfvars
resource_prefix=$(read_tfvar resource_prefix)
environment=$(read_tfvar environment)
location=$(read_tfvar location)
state_count=$(terraform_state_count)
prepare_resource_prefix
validate_generated_names
initialize_operation_evidence

case "$action" in
deploy)
    deploy
    ;;
undeploy)
    undeploy
    ;;
esac
