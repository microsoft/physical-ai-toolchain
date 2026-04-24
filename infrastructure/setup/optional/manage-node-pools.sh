#!/usr/bin/env bash
# Manage AKS GPU node pools (and their OSMO pool/platform configs) on an existing OSMO install
# without redeploying infrastructure or the OSMO control plane.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

SETUP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MANAGED_TFVARS_NAME="node-pools.managed.auto.tfvars.json"

show_help() {
  cat << EOF
Usage: $(basename "$0") <command> [OPTIONS]

Add, remove, list, or re-sync AKS GPU node pools on an existing cluster.
Node pools are driven by Terraform's \`node_pools\` variable; this tool
maintains a managed overlay file (${MANAGED_TFVARS_NAME}) and reconciles
OSMO POD_TEMPLATE, POOL, and BACKEND configs via 04-deploy-osmo-backend.sh.

COMMANDS:
    list                         Show configured node pools
    add    --name N ...          Create a new node pool
    remove --name N              Destroy a node pool
    sync                         Re-render OSMO pool configs from current Terraform state

COMMON OPTIONS:
    -h, --help                   Show this help message
    -t, --tf-dir DIR             Terraform directory (default: $DEFAULT_TF_DIR)
    --skip-apply                 Skip 'terraform apply' (add/remove only)
    --skip-osmo-sync             Skip OSMO config reconciliation (add/remove only)
    --osmo-args ARGS             Extra args forwarded to 04-deploy-osmo-backend.sh
                                 (quote the whole string, e.g. --osmo-args '--use-acr')

ADD OPTIONS (required except where noted):
    --name NAME                  Pool name (used as Terraform map key and AKS node pool name)
    --vm-size SIZE               Azure VM size (e.g. Standard_D8ds_v5)
    --subnet CIDR                Subnet address prefix (must not overlap existing subnets)
    --priority P                 Regular | Spot (default: Regular)
    --node-count N               Fixed node count (omit with --auto-scale)
    --auto-scale                 Enable cluster autoscaler on this pool
    --min-count N                Min nodes (with --auto-scale)
    --max-count N                Max nodes (with --auto-scale)
    --eviction-policy P          Delete | Deallocate (Spot only, default: Delete)
    --gpu-driver D               Install | None (default: omit; only set for GPU pools)
    --taint KEY=VAL:EFFECT       Node taint; repeatable (default: none)
    --label KEY=VAL              Node label; repeatable (default: none)
    --zone Z                     Availability zone; repeatable (default: none)

EXAMPLES:
    # List current pools
    $(basename "$0") list

    # Add a CPU pool large enough for SDG workflows that need > 4 vCPU
    $(basename "$0") add --name sdgcpu \\
      --vm-size Standard_D8ds_v5 --subnet 10.0.12.0/24 \\
      --node-count 1 --osmo-args '--use-acr'

    # Add a spot GPU pool with autoscaling
    $(basename "$0") add --name h100spot \\
      --vm-size Standard_NC40ads_H100_v5 --subnet 10.0.13.0/24 \\
      --priority Spot --eviction-policy Delete \\
      --auto-scale --min-count 0 --max-count 2 \\
      --taint 'nvidia.com/gpu=:NoSchedule' \\
      --taint 'kubernetes.azure.com/scalesetpriority=spot:NoSchedule' \\
      --label 'kubernetes.azure.com/scalesetpriority=spot' \\
      --gpu-driver Install

    # Remove a pool (node pool + subnet + OSMO pool config)
    $(basename "$0") remove --name h100spot --osmo-args '--use-acr'

    # Re-sync OSMO configs after editing terraform.tfvars manually
    $(basename "$0") sync --osmo-args '--use-acr'
EOF
}

#------------------------------------------------------------------------------
# Argument parsing
#------------------------------------------------------------------------------

[[ $# -ge 1 ]] || { show_help; exit 1; }
command="$1"; shift
[[ "$command" == "-h" || "$command" == "--help" ]] && { show_help; exit 0; }

# Defaults
tf_dir="$SETUP_DIR/$DEFAULT_TF_DIR"
pool_name=""
vm_size=""
subnet_cidr=""
priority="Regular"
node_count=""
auto_scale=false
min_count=""
max_count=""
eviction_policy="Delete"
gpu_driver=""
taints=()
labels=()
zones=()
skip_apply=false
skip_osmo_sync=false
osmo_args=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -t|--tf-dir)         tf_dir="$2"; shift 2 ;;
    --name)              pool_name="$2"; shift 2 ;;
    --vm-size)           vm_size="$2"; shift 2 ;;
    --subnet)            subnet_cidr="$2"; shift 2 ;;
    --priority)          priority="$2"; shift 2 ;;
    --node-count)        node_count="$2"; shift 2 ;;
    --auto-scale)        auto_scale=true; shift ;;
    --min-count)         min_count="$2"; shift 2 ;;
    --max-count)         max_count="$2"; shift 2 ;;
    --eviction-policy)   eviction_policy="$2"; shift 2 ;;
    --gpu-driver)        gpu_driver="$2"; shift 2 ;;
    --taint)             taints+=("$2"); shift 2 ;;
    --label)             labels+=("$2"); shift 2 ;;
    --zone)              zones+=("$2"); shift 2 ;;
    --skip-apply)        skip_apply=true; shift ;;
    --skip-osmo-sync)    skip_osmo_sync=true; shift ;;
    --osmo-args)         osmo_args="$2"; shift 2 ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

require_tools terraform jq

# Ensure terraform directory and overlay file path resolve now that tf_dir is final
tf_dir="$(cd "$tf_dir" && pwd)" || fatal "Terraform directory not found: $tf_dir"
managed_tfvars="$tf_dir/$MANAGED_TFVARS_NAME"

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

current_pools_json() {
  # Emit effective var.node_pools as compact JSON by asking terraform to evaluate it.
  # This captures whatever Terraform currently sees, whether from terraform.tfvars,
  # the managed overlay, or any other *.auto.tfvars file.
  local raw
  raw=$(printf 'jsonencode(var.node_pools)\n' | terraform -chdir="$tf_dir" console 2>/dev/null) \
    || fatal "terraform console failed; run 'terraform init' in $tf_dir and retry"
  # terraform console emits the JSON wrapped as a quoted string literal
  echo "$raw" | jq -r .
}

seed_managed_tfvars() {
  # On first write, snapshot the current effective pool map so the overlay
  # takes over the full set (Terraform applies the last-loaded value for a variable).
  if [[ -f "$managed_tfvars" ]]; then
    return
  fi
  info "Seeding $MANAGED_TFVARS_NAME from current var.node_pools"
  local pools
  pools=$(current_pools_json)
  jq -n --argjson pools "$pools" '{node_pools: $pools}' > "$managed_tfvars"
}

load_managed_pools() {
  seed_managed_tfvars
  jq '.node_pools' "$managed_tfvars"
}

save_managed_pools() {
  local pools_json="$1"
  jq -n --argjson pools "$pools_json" '{node_pools: $pools}' > "$managed_tfvars"
}

build_new_pool_entry() {
  # Compose the Terraform node_pools map entry for --add from CLI flags.
  local subnet_prefixes taints_json labels_json zones_json
  subnet_prefixes=$(jq -n --arg c "$subnet_cidr" '[$c]')

  if [[ ${#taints[@]} -eq 0 ]]; then
    taints_json='[]'
  else
    taints_json=$(printf '%s\n' "${taints[@]}" | jq -Rn '[inputs]')
  fi

  if [[ ${#labels[@]} -eq 0 ]]; then
    labels_json='{}'
  else
    labels_json=$(
      printf '%s\n' "${labels[@]}" \
        | jq -Rn '[inputs | capture("^(?<k>[^=]+)=(?<v>.*)$")] | from_entries'
    )
  fi

  if [[ ${#zones[@]} -eq 0 ]]; then
    zones_json='[]'
  else
    zones_json=$(printf '%s\n' "${zones[@]}" | jq -Rn '[inputs]')
  fi

  local node_count_json="null"
  [[ -n "$node_count" ]] && node_count_json="$node_count"

  local min_json="null" max_json="null"
  [[ -n "$min_count" ]] && min_json="$min_count"
  [[ -n "$max_count" ]] && max_json="$max_count"

  local eviction_json="null"
  [[ "$priority" == "Spot" ]] && eviction_json="$(jq -Rn --arg v "$eviction_policy" '$v')"

  local gpu_driver_json="null"
  [[ -n "$gpu_driver" ]] && gpu_driver_json="$(jq -Rn --arg v "$gpu_driver" '$v')"

  jq -n \
    --arg vm_size "$vm_size" \
    --argjson subnet_address_prefixes "$subnet_prefixes" \
    --argjson node_taints "$taints_json" \
    --argjson node_labels "$labels_json" \
    --argjson zones "$zones_json" \
    --argjson node_count "$node_count_json" \
    --arg priority "$priority" \
    --argjson should_enable_auto_scaling "$auto_scale" \
    --argjson min_count "$min_json" \
    --argjson max_count "$max_json" \
    --argjson eviction_policy "$eviction_json" \
    --argjson gpu_driver "$gpu_driver_json" \
    '{
      vm_size: $vm_size,
      subnet_address_prefixes: $subnet_address_prefixes,
      node_taints: $node_taints,
      node_labels: $node_labels,
      zones: $zones,
      node_count: $node_count,
      priority: $priority,
      should_enable_auto_scaling: $should_enable_auto_scaling,
      min_count: $min_count,
      max_count: $max_count,
      eviction_policy: $eviction_policy,
      gpu_driver: $gpu_driver
    } | with_entries(select(.value != null))'
}

run_terraform_apply() {
  if [[ "$skip_apply" == "true" ]]; then
    warn "Skipping 'terraform apply' (--skip-apply)"
    return
  fi
  section "terraform apply"
  info "Applying Terraform in $tf_dir (only new/removed node pool resources will change)"
  terraform -chdir="$tf_dir" apply -auto-approve
}

run_osmo_sync() {
  if [[ "$skip_osmo_sync" == "true" ]]; then
    warn "Skipping OSMO config sync (--skip-osmo-sync)"
    info "To reconcile manually: $SETUP_DIR/04-deploy-osmo-backend.sh $osmo_args"
    return
  fi
  section "OSMO config sync"
  # shellcheck disable=SC2086  # intentional word-splitting of user-provided args
  bash "$SETUP_DIR/04-deploy-osmo-backend.sh" --tf-dir "$tf_dir" $osmo_args
}

#------------------------------------------------------------------------------
# Commands
#------------------------------------------------------------------------------

cmd_list() {
  section "Configured node pools"
  local pools
  pools=$(current_pools_json)
  if [[ "$(echo "$pools" | jq 'length')" == "0" ]]; then
    info "No pools configured"
    return
  fi
  printf '%-20s %-36s %-10s %-8s %-10s %s\n' NAME VM_SIZE PRIORITY AUTOSCALE COUNT TAINTS
  echo "$pools" | jq -r '
    to_entries[] |
    [
      .key,
      .value.vm_size,
      (.value.priority // "Regular"),
      (.value.should_enable_auto_scaling // false | tostring),
      (
        if (.value.should_enable_auto_scaling // false) then
          "\(.value.min_count // 0)-\(.value.max_count // 0)"
        else
          (.value.node_count // 0 | tostring)
        end
      ),
      ((.value.node_taints // []) | join(","))
    ] | @tsv
  ' | while IFS=$'\t' read -r name vm prio auto count tnt; do
    printf '%-20s %-36s %-10s %-8s %-10s %s\n' "$name" "$vm" "$prio" "$auto" "$count" "$tnt"
  done
}

cmd_add() {
  [[ -n "$pool_name" ]]   || fatal "--name is required"
  [[ -n "$vm_size" ]]     || fatal "--vm-size is required"
  [[ -n "$subnet_cidr" ]] || fatal "--subnet is required"

  case "$priority" in
    Regular|Spot) ;;
    *) fatal "--priority must be 'Regular' or 'Spot' (got '$priority')" ;;
  esac

  if [[ "$auto_scale" == "true" ]]; then
    [[ -n "$min_count" && -n "$max_count" ]] || fatal "--auto-scale requires --min-count and --max-count"
    [[ -z "$node_count" ]] || fatal "--node-count cannot be combined with --auto-scale"
  else
    [[ -n "$node_count" ]] || fatal "--node-count is required when --auto-scale is not set"
  fi

  local existing new_entry merged
  existing=$(load_managed_pools)

  if [[ "$(echo "$existing" | jq --arg n "$pool_name" 'has($n)')" == "true" ]]; then
    fatal "Pool '$pool_name' already exists. Use 'remove' first or pick a different name."
  fi

  new_entry=$(build_new_pool_entry)
  merged=$(jq --arg n "$pool_name" --argjson e "$new_entry" '. + {($n): $e}' <<< "$existing")

  section "Adding node pool '$pool_name'"
  print_kv "VM size" "$vm_size"
  print_kv "Subnet" "$subnet_cidr"
  print_kv "Priority" "$priority"
  print_kv "Autoscale" "$([[ $auto_scale == true ]] && echo "true ($min_count..$max_count)" || echo "false ($node_count nodes)")"
  [[ ${#taints[@]} -gt 0 ]] && print_kv "Taints" "${taints[*]}"
  [[ ${#labels[@]} -gt 0 ]] && print_kv "Labels" "${labels[*]}"

  save_managed_pools "$merged"
  info "Wrote overlay: $managed_tfvars"

  run_terraform_apply
  run_osmo_sync

  section "Summary"
  print_kv "Pool" "$pool_name"
  print_kv "Overlay" "$managed_tfvars"
  info "Pool '$pool_name' added. Verify with: kubectl get nodes -l agentpool=$pool_name"
}

cmd_remove() {
  [[ -n "$pool_name" ]] || fatal "--name is required"

  local existing
  existing=$(load_managed_pools)

  if [[ "$(echo "$existing" | jq --arg n "$pool_name" 'has($n)')" != "true" ]]; then
    fatal "Pool '$pool_name' not found in managed overlay. Run 'list' to see pools."
  fi

  local remaining_count
  remaining_count=$(echo "$existing" | jq --arg n "$pool_name" 'del(.[$n]) | length')
  if [[ "$remaining_count" -eq 0 ]]; then
    warn "Removing '$pool_name' will leave zero GPU/workload pools; OSMO workflows will have no pool to run on."
  fi

  if [[ -n "${DEFAULT_POOL:-}" && "$DEFAULT_POOL" == "$pool_name" ]]; then
    warn "DEFAULT_POOL='$DEFAULT_POOL' matches the pool being removed. Update .env.local or OSMO sync will fail."
  fi

  section "Removing node pool '$pool_name'"
  local merged
  merged=$(echo "$existing" | jq --arg n "$pool_name" 'del(.[$n])')
  save_managed_pools "$merged"
  info "Updated overlay: $managed_tfvars"

  run_terraform_apply
  run_osmo_sync

  section "Summary"
  print_kv "Removed pool" "$pool_name"
  print_kv "Overlay" "$managed_tfvars"
}

cmd_sync() {
  section "Re-sync OSMO configs"
  skip_apply=true
  run_osmo_sync
}

case "$command" in
  list)   cmd_list ;;
  add)    cmd_add ;;
  remove) cmd_remove ;;
  sync)   cmd_sync ;;
  *)      fatal "Unknown command: $command (expected: list|add|remove|sync)" ;;
esac
