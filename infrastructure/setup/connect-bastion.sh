#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
#
# Connect to private Azure resources through Azure Bastion Standard native tunneling.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [COMMAND] [OPTIONS]

Connect to a private jumpbox or internal Azure service through Azure Bastion Standard.

COMMANDS:
    ssh                         Open an interactive SSH session via Azure Bastion (default)
    tunnel                      Open a native TCP tunnel to a target resource port
    proxy                       Open a Bastion tunnel and forward local ports through SSH (-L)

OPTIONS:
    -h, --help                  Show this help message
    -t, --tf-dir DIR            Bastion Terraform directory (default: $DEFAULT_BASTION_TF_DIR)
    --subscription-id ID        Azure subscription ID (env: AZURE_SUBSCRIPTION_ID)
    -g, --resource-group NAME   Resource group containing Bastion (env: AZURE_RESOURCE_GROUP)
    -n, --bastion-name NAME     Azure Bastion host name (env: BASTION_NAME)
    --target-resource-id ID     Target Azure VM resource ID (env: JUMPBOX_RESOURCE_ID)
    --jumpbox-name NAME         Jumpbox VM name to derive target ID (env: JUMPBOX_VM_NAME)
    --auth-type TYPE            SSH auth mode: AAD or ssh-key (default: AAD)
    -u, --username USER         Linux username for ssh-key or proxy mode (env: JUMPBOX_ADMIN_USERNAME)
    --ssh-key PATH              Private SSH key path (must be owner-only, e.g. chmod 600)
    --resource-port PORT        Remote port on target resource (default: $BASTION_DEFAULT_SSH_PORT)
    --local-port PORT           Local TCP port for tunnel/proxy (default: $BASTION_DEFAULT_LOCAL_PORT)
    -L, --forward SPEC          SSH port-forward for proxy mode: <local>:<host>:<remote> (repeatable)
    --wait-seconds SECONDS      Max seconds to wait for local Bastion tunnel (default: $BASTION_TUNNEL_WAIT_SECONDS)
    --config-preview            Print resolved configuration and exit
    --dry-run                   Print the Azure CLI / SSH commands without executing

EXAMPLES:
    $(basename "$0") ssh
    $(basename "$0") ssh --resource-group rg-robotics-dev-001 --bastion-name bas-robotics-dev-001 \\
      --target-resource-id /subscriptions/<sub-id>/resourceGroups/rg-robotics-dev-001/providers/Microsoft.Compute/virtualMachines/vm-jb-robotics-dev-001
    $(basename "$0") tunnel --resource-port 22 --local-port 50022
    $(basename "$0") proxy --username azureuser --ssh-key ~/.ssh/id_ed25519 \\
      --forward 8080:osmo.internal.example:80 --forward 8000:dataviewer.internal.example:8000
EOF
}

validate_port() {
  local port="$1" label="$2"
  [[ "$port" =~ ^[0-9]+$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )) || \
    fatal "$label must be a valid TCP port (1-65535): $port"
}

validate_positive_int() {
  local value="$1" label="$2"
  [[ "$value" =~ ^[0-9]+$ ]] && (( 10#$value >= 1 )) || \
    fatal "$label must be a positive integer: $value"
}

validate_forward_spec() {
  local spec="$1" local_p remote_host remote_p rest
  IFS=':' read -r local_p remote_host remote_p rest <<< "$spec"
  [[ -n "$local_p" && -n "$remote_host" && -n "$remote_p" && -z "$rest" ]] || \
    fatal "Invalid --forward specification '$spec' (expected <local_port>:<remote_host>:<remote_port>)"
  validate_port "$local_p" "--forward local port"
  validate_port "$remote_p" "--forward remote port"
  [[ "$remote_host" =~ ^[A-Za-z0-9._-]+$ ]] || \
    fatal "Invalid --forward remote host '$remote_host'"
}

format_command() {
  local arg formatted=""
  for arg in "$@"; do
    formatted+="$(printf '%q ' "$arg")"
  done
  printf '%s' "${formatted% }"
}

wait_for_local_port() {
  local port="$1" timeout_sec="$2" tunnel_pid="$3" elapsed=0
  while (( elapsed < timeout_sec )); do
    if ! kill -0 "$tunnel_pid" 2>/dev/null; then
      fatal "Azure Bastion tunnel process exited unexpectedly before port $port became ready"
    fi
    if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
      exec 3>&- 2>/dev/null || true
      return 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  fatal "Timed out after ${timeout_sec}s waiting for local Bastion tunnel on 127.0.0.1:${port}"
}

# Defaults
command_mode="ssh"
command_set=false
tf_dir="$SCRIPT_DIR/$DEFAULT_BASTION_TF_DIR"
tf_dir_explicit=false

cli_subscription_id=""
cli_resource_group=""
cli_bastion_name=""
cli_target_resource_id=""
cli_jumpbox_name=""
cli_auth_type=""
cli_username=""
cli_ssh_key=""
cli_resource_port=""
cli_local_port=""
cli_wait_seconds=""

forwards=()
config_preview=false
dry_run=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    ssh|tunnel|proxy)
      if [[ "$command_set" == "true" ]]; then
        fatal "Command already specified as '$command_mode'; unexpected argument: $1"
      fi
      command_mode="$1"
      command_set=true
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    -t|--tf-dir)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      tf_dir="$2"
      tf_dir_explicit=true
      shift 2
      ;;
    --subscription-id)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_subscription_id="$2"
      shift 2
      ;;
    -g|--resource-group)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_resource_group="$2"
      shift 2
      ;;
    -n|--bastion-name)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_bastion_name="$2"
      shift 2
      ;;
    --target-resource-id)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_target_resource_id="$2"
      shift 2
      ;;
    --jumpbox-name)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_jumpbox_name="$2"
      shift 2
      ;;
    --auth-type)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_auth_type="$2"
      shift 2
      ;;
    -u|--username)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_username="$2"
      shift 2
      ;;
    --ssh-key)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_ssh_key="$2"
      shift 2
      ;;
    --resource-port)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_resource_port="$2"
      shift 2
      ;;
    --local-port)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_local_port="$2"
      shift 2
      ;;
    -L|--forward)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      forwards+=("$2")
      shift 2
      ;;
    --wait-seconds)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_wait_seconds="$2"
      shift 2
      ;;
    --config-preview)
      config_preview=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    -*)
      fatal "Unknown option: $1"
      ;;
    *)
      fatal "Unknown command or argument: $1"
      ;;
  esac
done

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

subscription_id="${cli_subscription_id:-${AZURE_SUBSCRIPTION_ID:-}}"
resource_group="${cli_resource_group:-${AZURE_RESOURCE_GROUP:-}}"
bastion_name="${cli_bastion_name:-${BASTION_NAME:-}}"
target_resource_id="${cli_target_resource_id:-${JUMPBOX_RESOURCE_ID:-}}"
jumpbox_name="${cli_jumpbox_name:-${JUMPBOX_VM_NAME:-}}"
auth_type="${cli_auth_type:-AAD}"
username="${cli_username:-${JUMPBOX_ADMIN_USERNAME:-}}"
ssh_key="${cli_ssh_key:-}"
resource_port="${cli_resource_port:-$BASTION_DEFAULT_SSH_PORT}"
local_port="${cli_local_port:-$BASTION_DEFAULT_LOCAL_PORT}"
wait_seconds="${cli_wait_seconds:-$BASTION_TUNNEL_WAIT_SECONDS}"

needs_username=false
if [[ "$command_mode" == "proxy" || ( "$command_mode" == "ssh" && "$auth_type" == "ssh-key" ) ]]; then
  needs_username=true
fi

needs_tf_outputs=false
if [[ "$tf_dir_explicit" == "true" ]]; then
  needs_tf_outputs=true
elif [[ -z "$resource_group" || -z "$bastion_name" || -z "$target_resource_id" || ( "$needs_username" == "true" && -z "$username" ) ]]; then
  if [[ -f "$tf_dir/terraform.tfstate" ]] && command -v terraform &>/dev/null; then
    needs_tf_outputs=true
  fi
fi

if [[ "$needs_tf_outputs" == "true" ]]; then
  require_tools terraform jq
  tf_output=$(read_terraform_outputs "$tf_dir")
  [[ -n "$resource_group" ]] || resource_group=$(tf_get "$tf_output" "connection_info.value.resource_group_name" "")
  [[ -n "$bastion_name" ]] || bastion_name=$(tf_get "$tf_output" "connection_info.value.bastion_name" "")
  [[ -n "$target_resource_id" ]] || target_resource_id=$(tf_get "$tf_output" "connection_info.value.jumpbox_id" "")
  [[ -n "$jumpbox_name" ]] || jumpbox_name=$(tf_get "$tf_output" "connection_info.value.jumpbox_name" "")
  [[ -n "$username" ]] || username=$(tf_get "$tf_output" "connection_info.value.jumpbox_admin_username" "")
fi

if [[ -z "$target_resource_id" && -n "$subscription_id" && -n "$resource_group" && -n "$jumpbox_name" ]]; then
  target_resource_id="/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.Compute/virtualMachines/${jumpbox_name}"
fi

# Validate inputs
case "$auth_type" in
  AAD|ssh-key) ;;
  *) fatal "--auth-type must be 'AAD' or 'ssh-key': $auth_type" ;;
esac

validate_port "$resource_port" "--resource-port"
validate_port "$local_port" "--local-port"
validate_positive_int "$wait_seconds" "--wait-seconds"

if [[ ${#forwards[@]} -gt 0 ]]; then
  for spec in "${forwards[@]}"; do
    validate_forward_spec "$spec"
  done
fi

[[ -n "$resource_group" ]] || fatal "Resource group is required (pass --resource-group, set AZURE_RESOURCE_GROUP, or supply --tf-dir)"
[[ -n "$bastion_name" ]] || fatal "Bastion name is required (pass --bastion-name, set BASTION_NAME, or supply --tf-dir)"
[[ -n "$target_resource_id" ]] || fatal "Target resource ID is required (pass --target-resource-id, set JUMPBOX_RESOURCE_ID, or supply --tf-dir)"

if [[ "$command_mode" == "ssh" && "$auth_type" == "ssh-key" ]]; then
  [[ -n "$username" ]] || fatal "--username is required when --auth-type is ssh-key"
  [[ -n "$ssh_key" ]] || fatal "--ssh-key is required when --auth-type is ssh-key"
  require_protected_file "$ssh_key"
fi

if [[ "$command_mode" == "proxy" ]]; then
  [[ ${#forwards[@]} -gt 0 ]] || fatal "At least one --forward <local_port>:<remote_host>:<remote_port> is required for proxy mode"
  [[ -n "$username" ]] || fatal "--username is required for proxy mode"
  if [[ -n "$ssh_key" ]]; then
    require_protected_file "$ssh_key"
  fi
fi

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Command" "$command_mode"
  print_kv "Subscription" "${subscription_id:-<active az account>}"
  print_kv "Resource Group" "$resource_group"
  print_kv "Bastion Host" "$bastion_name"
  print_kv "Target Resource ID" "$target_resource_id"
  if [[ "$command_mode" == "ssh" ]]; then
    print_kv "Auth Type" "$auth_type"
    [[ -n "$username" ]] && print_kv "Username" "$username"
    [[ -n "$ssh_key" ]] && print_kv "SSH Key" "$ssh_key"
  else
    print_kv "Resource Port" "$resource_port"
    print_kv "Local Port" "$local_port"
  fi
  if [[ "$command_mode" == "proxy" ]]; then
    print_kv "Username" "$username"
    [[ -n "$ssh_key" ]] && print_kv "SSH Key" "$ssh_key"
    print_kv "Tunnel Wait" "${wait_seconds}s"
    print_kv "Forwards" "${forwards[*]}"
  fi
  exit 0
fi

#------------------------------------------------------------------------------
# Build Commands & Execute
#------------------------------------------------------------------------------

if [[ "$dry_run" != "true" ]]; then
  require_tools az
  if [[ "$command_mode" == "proxy" ]]; then
    require_tools ssh
  fi
  require_az_extension bastion
fi

if [[ "$command_mode" == "ssh" ]]; then
  az_args=(
    network bastion ssh
    --name "$bastion_name"
    --resource-group "$resource_group"
    --target-resource-id "$target_resource_id"
    --auth-type "$auth_type"
  )
  [[ -n "$subscription_id" ]] && az_args+=(--subscription "$subscription_id")
  if [[ "$auth_type" == "ssh-key" ]]; then
    az_args+=(--username "$username" --ssh-key "$ssh_key")
  fi

  section "Azure Bastion SSH"
  print_kv "Bastion Host" "$bastion_name"
  print_kv "Auth Type" "$auth_type"

  if [[ "$dry_run" == "true" ]]; then
    info "Dry run: $(format_command az "${az_args[@]}")"
    exit 0
  fi

  exec az "${az_args[@]}"
fi

if [[ "$command_mode" == "tunnel" ]]; then
  az_args=(
    network bastion tunnel
    --name "$bastion_name"
    --resource-group "$resource_group"
    --target-resource-id "$target_resource_id"
    --resource-port "$resource_port"
    --port "$local_port"
  )
  [[ -n "$subscription_id" ]] && az_args+=(--subscription "$subscription_id")

  section "Azure Bastion Tunnel"
  print_kv "Bastion Host" "$bastion_name"
  print_kv "Local Endpoint" "127.0.0.1:${local_port} -> remote:${resource_port}"

  if [[ "$dry_run" == "true" ]]; then
    info "Dry run: $(format_command az "${az_args[@]}")"
    exit 0
  fi

  exec az "${az_args[@]}"
fi

# Proxy mode: Bastion tunnel + SSH local port-forwarding
tunnel_args=(
  network bastion tunnel
  --name "$bastion_name"
  --resource-group "$resource_group"
  --target-resource-id "$target_resource_id"
  --resource-port "$resource_port"
  --port "$local_port"
)
[[ -n "$subscription_id" ]] && tunnel_args+=(--subscription "$subscription_id")

ssh_args=(
  -N
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -p "$local_port"
)
[[ -n "$ssh_key" ]] && ssh_args+=(-i "$ssh_key")
for spec in "${forwards[@]}"; do
  ssh_args+=(-L "$spec")
done
ssh_args+=("${username}@127.0.0.1")

section "Azure Bastion Proxy"
print_kv "Bastion Host" "$bastion_name"
print_kv "Local Tunnel" "127.0.0.1:${local_port} -> remote:${resource_port}"
print_kv "Forwards" "${forwards[*]}"

if [[ "$dry_run" == "true" ]]; then
  info "Dry run (tunnel): $(format_command az "${tunnel_args[@]}")"
  info "Dry run (ssh): $(format_command ssh "${ssh_args[@]}")"
  exit 0
fi

tunnel_pid=""
cleanup_tunnel() {
  if [[ -n "$tunnel_pid" ]] && kill -0 "$tunnel_pid" 2>/dev/null; then
    kill "$tunnel_pid" 2>/dev/null || true
    wait "$tunnel_pid" 2>/dev/null || true
  fi
}
trap cleanup_tunnel EXIT INT TERM

info "Starting Azure Bastion tunnel on 127.0.0.1:${local_port}..."
az "${tunnel_args[@]}" >/dev/null 2>&1 &
tunnel_pid=$!

wait_for_local_port "$local_port" "$wait_seconds" "$tunnel_pid"
info "Tunnel ready. Starting SSH port forwarding..."
ssh "${ssh_args[@]}"
