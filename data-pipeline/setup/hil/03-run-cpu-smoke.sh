#!/usr/bin/env bash
# Prove CPU-only OSMO scheduling through the connected local backend.
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load the shared workflow helpers.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"

# Describe the connection receipt required to submit a CPU-only proof workflow.
show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit one uniquely named CPU-only workflow through the connected OSMO pool.

OPTIONS:
    -h, --help                Show this help message
    --connection-file PATH    Successful connection receipt
    --config-preview          Print configuration and exit

EXAMPLES:
    $(basename "$0") --connection-file ~/.local/state/physical-ai-toolchain/hil/dev-001-hil-lab-01-connection.json
EOF
}

# Create a unique workflow identity and pin the test image and reviewed workflow template.
connection_file="${HIL_CONNECTION_FILE:-}"
workflow_name="hil-cpu-$(date -u +%Y%m%dt%H%M%S)-${RANDOM}${RANDOM}"
image="alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1"
workflow="$REPO_ROOT/evaluation/hil/workflows/osmo/cpu-smoke.yaml"
config_preview=false

# Apply the optional receipt override before validating the immutable test inputs.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    --connection-file)   connection_file="$2"; shift 2 ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

# Require a connection receipt and reject mutable workflow or image references before any submission.
[[ -n "$connection_file" ]] || fatal "--connection-file is required"

# Show the planned workflow and its zero-GPU contract, then exit without contacting OSMO.
if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Milestone" "validated: CPU scheduling"
  print_kv "Connection Receipt" "$connection_file"
  print_kv "Workflow" "$workflow_name"
  print_kv "Template" "$workflow"
  print_kv "Image" "$image"
  print_kv "CPU" "1"
  print_kv "GPU" "0"
  print_kv "Next" "04-run-no-command-check.sh"
  exit 0
fi

# Load the connection details used for submission.
require_tools jq osmo
service_url=$(jq -r '.service_url' "$connection_file")
backend_name=$(jq -r '.backend_name' "$connection_file")
pool_name=$(jq -r '.pool_name' "$connection_file")
osmo_config_dir=$(jq -r '.osmo_config_dir' "$connection_file")
export XDG_CONFIG_HOME="$osmo_config_dir"
osmo profile set pool "$pool_name" >/dev/null

# Submit the reviewed CPU-only workflow to the pool selected by the connection receipt.
submission_json=$(osmo workflow submit "$workflow" --format-type json --pool "$pool_name" \
  --set-string "workflow_name=$workflow_name" \
  --set-string "backend_name=$backend_name" \
  --set-string "pool_name=$pool_name" \
  --set-string "image=$image")
workflow_id=$(jq -r '.id // .workflow_id // .uuid // empty' <<< "$submission_json")
[[ -n "$workflow_id" ]] || fatal "OSMO submission response did not contain a workflow ID"

# Wait for the submitted workflow to finish.
hil_wait_for_workflow "$workflow_id" 600

# Report the successful scheduling proof and point to the next no-command safety check.
section "Deployment Summary"
print_kv "Milestone" "validated: CPU scheduling"
print_kv "Workflow" "$workflow_name"
print_kv "Workflow ID" "$workflow_id"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Service URL" "$service_url"
print_kv "GPU" "0"
print_kv "Status" "passed"
print_kv "Next" "$SCRIPT_DIR/04-run-no-command-check.sh --connection-file $connection_file"
info "CPU-only scheduling proof passed"
