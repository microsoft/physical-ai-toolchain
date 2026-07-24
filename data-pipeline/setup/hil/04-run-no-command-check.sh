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

verify_workflow_upload() {
  local workflow_id="${1:?workflow ID required}" output_uri="${2:?output URI required}"
  local workflow_query workflow_spec

  workflow_query=$(osmo workflow query "$workflow_id" --format-type json)
  jq -e '
    .status == "COMPLETED" and
    (.outputs | type == "string" and length > 0) and
    ([.groups | if type == "array" then .[] else empty end |
      select(type == "object") |
      .tasks | if type == "array" then .[] else empty end |
      select(type == "object" and .name == "ur10e-no-command" and
        (.output_upload_start_time | type == "string" and length > 0))] | length == 1)
  ' <<< "$workflow_query" >/dev/null || \
    fatal "OSMO workflow query does not confirm the expected completed output upload"

  if ! workflow_spec=$(osmo workflow spec "$workflow_id"); then
    fatal "Unable to retrieve the rendered OSMO workflow specification"
  fi
  grep -Fq -- "$output_uri" <<< "$workflow_spec" || \
    fatal "Rendered OSMO workflow specification does not contain the expected output URI"
}

verify_downloaded_output() {
  local download_dir="${1:?download directory required}"
  local manifest_path artifact_dir artifact_path relative_path expected_bytes expected_sha actual_bytes
  local expected_file manifest_file path
  local expected_files=(observations.jsonl proposed-actions.jsonl safety-events.jsonl summary.json manifest.json)
  local manifest_paths=() downloaded_files=()

  # Downloaded output crosses a trust boundary: reject links and nested paths before opening artifacts.
  while IFS= read -r -d '' path; do
    fatal "OSMO output download contains a symbolic link: $path"
  done < <(find "$download_dir" -type l -print0)
  while IFS= read -r -d '' path; do
    manifest_paths+=("$path")
  done < <(find "$download_dir" -type f -name manifest.json -print0)
  (( ${#manifest_paths[@]} == 1 )) || fatal "OSMO output download must contain exactly one manifest.json"
  manifest_path="${manifest_paths[0]}"
  artifact_dir=$(dirname "$manifest_path")

  jq -e '
    (keys | sort) == ["files", "schema_version"] and
    .schema_version == 1 and
    (.files | type == "array" and length == 4) and
    ([.files[].path] | length == (unique | length)) and
    ([.files[].path] | sort) ==
      ["observations.jsonl", "proposed-actions.jsonl", "safety-events.jsonl", "summary.json"] and
    all(.files[];
      (keys | sort) == ["bytes", "path", "sha256"] and
      (.path | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9._-]*$") and . != "." and . != "..") and
      (.bytes | type == "number" and . >= 0 and floor == .) and
      (.sha256 | type == "string" and test("^[0-9a-f]{64}$")))
  ' "$manifest_path" >/dev/null || fatal "OSMO output manifest does not match the no-command artifact contract"

  while IFS= read -r -d '' path; do
    relative_path="${path#"$artifact_dir"/}"
    [[ "$relative_path" != "$path" && "$relative_path" != */* ]] || \
      fatal "OSMO output download has an unexpected directory shape: $path"
    downloaded_files+=("$relative_path")
  done < <(find "$download_dir" -type f -print0)
  (( ${#downloaded_files[@]} == ${#expected_files[@]} )) || \
    fatal "OSMO output download does not contain exactly the expected artifacts"

  # The manifest authenticates the expected artifact set before safety semantics are evaluated.
  for expected_file in "${expected_files[@]}"; do
    artifact_path="$artifact_dir/$expected_file"
    [[ -f "$artifact_path" && ! -L "$artifact_path" ]] || \
      fatal "OSMO output artifact is missing or not a regular file: $expected_file"
  done
  for manifest_file in observations.jsonl proposed-actions.jsonl safety-events.jsonl summary.json; do
    artifact_path="$artifact_dir/$manifest_file"
    expected_bytes=$(jq -r --arg path "$manifest_file" '.files[] | select(.path == $path) | .bytes' "$manifest_path")
    expected_sha=$(jq -r --arg path "$manifest_file" '.files[] | select(.path == $path) | .sha256' "$manifest_path")
    actual_bytes=$(wc -c < "$artifact_path" | tr -d ' ')
    [[ "$actual_bytes" == "$expected_bytes" ]] || fatal "OSMO output byte count mismatch: $manifest_file"
    [[ "$(calculate_sha256 "$artifact_path")" == "$expected_sha" ]] || \
      fatal "OSMO output digest mismatch: $manifest_file"
  done

  jq -e '
    .schema_version == 1 and .kind == "ur10e-no-command-result" and
    .status == "passed" and .command_transport == "none" and
    .applied_actions == 0 and (.proposed_actions | type == "number" and . > 0) and
    .negative_command_probe == "passed" and .rejection_code == "NO_COMMAND_TRANSPORT"
  ' "$artifact_dir/summary.json" >/dev/null || \
    fatal "No-command summary does not prove the expected safety boundary"

  for artifact_path in "$manifest_path" "$artifact_dir/observations.jsonl" \
    "$artifact_dir/proposed-actions.jsonl" "$artifact_dir/safety-events.jsonl" "$artifact_dir/summary.json"; do
    hil_reject_credential_material "$artifact_path"
  done
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
require_tools jq
hil_validate_connection_receipt "$connection_file"
image=$(jq -r '.policy.image // empty' "$config")
receipt_fields=$(jq -er '
  [.backend_name, .pool_name, .service_url, .osmo_config_dir, .azure_config_dir,
   .osmo_workflow_data_uri, .osmo_workload_identity_client_id] | @tsv
' "$connection_file") || fatal "Unable to read the validated HiL connection receipt"
IFS=$'\t' read -r backend_name pool_name service_url osmo_config_dir azure_config_dir \
  workflow_data_uri workload_identity_client_id <<< "$receipt_fields"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || \
  fatal "No-command policy image must be pinned by SHA-256 digest"
output_uri="${workflow_data_uri%/}/hil/no-command/${workflow_name}"

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
  print_kv "Output URI" "$output_uri"
  print_kv "OSMO Identity Client ID" "$workload_identity_client_id"
  print_kv "Proposed Actions" "representative"
  print_kv "Applied Actions" "0 required"
  print_kv "Command Transport" "none"
  print_kv "Next" "stop; physical motion is unsupported"
  exit 0
fi

# Load the connection details used for submission.
require_tools base64 osmo
export XDG_CONFIG_HOME="$osmo_config_dir"
export AZURE_CONFIG_DIR="$azure_config_dir"
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
  --set-string "observations_b64=$observations_b64" \
  --set-string "output_uri=$output_uri")
unset runner_b64 config_b64 observations_b64
workflow_id=$(jq -r '.name // empty' <<< "$submission_json")
[[ -n "$workflow_id" ]] || fatal "OSMO submission response did not contain a workflow name"

# Wait for the submitted workflow to finish.
hil_wait_for_workflow "$workflow_id" 600

# Query metadata proves upload ran; the rendered spec proves this workflow declared the exact destination.
verify_workflow_upload "$workflow_id" "$output_uri"

temp_results_dir=$(mktemp -d)
cleanup() {
  rm -rf "$temp_results_dir"
}
trap cleanup EXIT
chmod 0700 "$temp_results_dir"
osmo data download "$output_uri" "$temp_results_dir"
verify_downloaded_output "$temp_results_dir"

# Report the passed no-command boundary and the explicit physical-motion limitation.
section "Deployment Summary"
print_kv "Milestone" "validated: no-command safety"
print_kv "Workflow" "$workflow_name"
print_kv "Workflow ID" "$workflow_id"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Service URL" "$service_url"
print_kv "Output URI" "$output_uri"
print_kv "Verified Artifacts" "5"
print_kv "Applied Actions" "0"
print_kv "Command Transport" "none"
print_kv "Status" "passed"
print_kv "Journey" "complete for CPU and no-command workloads"
print_kv "Physical Motion" "unsupported"
info "No-command safety proof passed"
