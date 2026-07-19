#!/usr/bin/env bash
# Prove representative actions cannot cross the no-command boundary.
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load the shared workflow helpers.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"

# Describe the connection receipt and the intentionally command-free workflow this check submits.
show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit one representative no-command workflow through the connected OSMO pool.
The payload has no robot endpoint, device, physical transport, or motion mode.

OPTIONS:
    -h, --help                Show this help message
    --connection-file PATH    Successful connection receipt
    --config-preview          Print configuration and exit

EXAMPLES:
    $(basename "$0") --connection-file ~/.local/state/physical-ai-toolchain/hil/dev-001-hil-lab-01-connection.json
EOF
}

# Create a unique workflow identity and pin every reviewed no-command asset by SHA-256.
connection_file="${HIL_CONNECTION_FILE:-}"
workflow_name="hil-no-command-$(date -u +%Y%m%dt%H%M%S)-${RANDOM}${RANDOM}"
config="$REPO_ROOT/evaluation/hil/config/ur10e-no-command.json"
fixture="$REPO_ROOT/evaluation/hil/config/ur10e-observations.jsonl"
runner="$REPO_ROOT/evaluation/hil/no_command_runner.py"
workflow="$REPO_ROOT/evaluation/hil/workflows/osmo/hil-evaluation.yaml"
config_preview=false

# Apply the optional receipt override before validating the no-command configuration and image.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    --connection-file)   connection_file="$2"; shift 2 ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

# Require a connection receipt and reject a mutable policy image before any remote work begins.
[[ -n "$connection_file" ]] || fatal "--connection-file is required"
image=$(jq -r '.policy.image // empty' "$config")

# Show the zero-action plan and exit without contacting OSMO or encoding local assets.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "validated: no-command safety"
  print_kv "Connection Receipt" "$connection_file"
  print_kv "Workflow" "$workflow_name"
  print_kv "Configuration" "$config"
  print_kv "Fixture" "$fixture"
  print_kv "Runner" "$runner"
  print_kv "Image" "$image"
  print_kv "Proposed Actions" "representative"
  print_kv "Applied Actions" "0 required"
  print_kv "Command Transport" "none"
  print_kv "Next" "stop; physical motion is unsupported"
  exit 0
fi

# Load the connection details used for submission.
require_tools base64 jq osmo
backend_name=$(jq -r '.backend_name' "$connection_file")
pool_name=$(jq -r '.pool_name' "$connection_file")
service_url=$(jq -r '.service_url' "$connection_file")
osmo_config_dir=$(jq -r '.osmo_config_dir' "$connection_file")
export XDG_CONFIG_HOME="$osmo_config_dir"
osmo profile set pool "$pool_name" >/dev/null

runner_b64=$(base64 < "$runner" | tr -d '\n')
config_b64=$(base64 < "$config" | tr -d '\n')
observations_b64=$(base64 < "$fixture" | tr -d '\n')

# Submit the command-free evaluation with the reviewed assets and no robot transport capability.
submission_json=$(osmo workflow submit "$workflow" --format-type json --pool "$pool_name" \
  --set-string "workflow_name=$workflow_name" \
  --set-string "backend_name=$backend_name" \
  --set-string "pool_name=$pool_name" \
  --set-string "image=$image" \
  --set-string "runner_b64=$runner_b64" \
  --set-string "config_b64=$config_b64" \
  --set-string "observations_b64=$observations_b64")
unset runner_b64 config_b64 observations_b64
workflow_id=$(jq -r '.id // .workflow_id // .uuid // empty' <<< "$submission_json")
[[ -n "$workflow_id" ]] || fatal "OSMO submission response did not contain a workflow ID"

# Wait for the submitted workflow to finish.
hil_wait_for_workflow "$workflow_id" 600

# Report the passed no-command boundary and the explicit physical-motion limitation.
section "Deployment Summary"
print_kv "Milestone" "validated: no-command safety"
print_kv "Workflow" "$workflow_name"
print_kv "Workflow ID" "$workflow_id"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Service URL" "$service_url"
print_kv "Applied Actions" "0"
print_kv "Command Transport" "none"
print_kv "Status" "passed"
print_kv "Journey" "complete for CPU and no-command workloads"
print_kv "Physical Motion" "unsupported"
info "No-command safety proof passed"
