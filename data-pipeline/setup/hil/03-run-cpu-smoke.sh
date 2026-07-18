#!/usr/bin/env bash
# Prove CPU-only OSMO scheduling through the connected local backend.
set -o errexit -o nounset -o pipefail

# Resolve repository paths and load the shared helpers used for receipt and node validation.
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
workflow_sha256="2af6657a82049a799902e0c1deedc1e19aaf6e711ea561d6ac993388d907be2c"
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
[[ "$workflow_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid workflow name: $workflow_name"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || fatal "CPU image pin is invalid"

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

# Validate the protected connection receipt and prove that its K3s identity matches the local host.
operation="validate successful OSMO connection"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: validated CPU scheduling"
  exit "$status"
}
trap report_failure ERR

require_tools jq osmo
[[ "$(calculate_sha256 "$workflow")" == "$workflow_sha256" ]] || fatal "CPU workflow differs from the reviewed asset"
require_external_runtime_path "$connection_file"
require_protected_file "$connection_file"
jq -e '
  .schema_version == 1 and .kind == "physical-ai-hil-connection" and
  (.service_url | test("^https?://[^[:space:]]+$")) and
  (.backend_name | test("^[a-z0-9-]+$")) and
  (.pool_name | test("^[a-z0-9-]+$")) and
  (.node_name | test("^[a-z0-9-]+$")) and
  (.workflow_namespace | test("^[a-z0-9-]+$")) and
  (.osmo_config_dir | type == "string" and length > 0)
' "$connection_file" >/dev/null || fatal "Connection receipt is invalid"
service_url=$(jq -r '.service_url' "$connection_file")
backend_name=$(jq -r '.backend_name' "$connection_file")
pool_name=$(jq -r '.pool_name' "$connection_file")
osmo_config_dir=$(jq -r '.osmo_config_dir' "$connection_file")
kubeconfig=$(jq -r '.kubeconfig' "$connection_file")
context=$(jq -r '.context' "$connection_file")
identity_file=$(jq -r '.k3s_identity_file' "$connection_file")
node_name=$(jq -r '.node_name' "$connection_file")
workflow_namespace=$(jq -r '.workflow_namespace' "$connection_file")
require_external_runtime_path "$identity_file"
require_external_runtime_path "$kubeconfig"
require_external_runtime_path "$osmo_config_dir"
require_protected_directory "$osmo_config_dir"
hil_require_local_k3s_identity "$identity_file" "$kubeconfig" "$context" "$node_name"
export XDG_CONFIG_HOME="$osmo_config_dir"
osmo profile set pool "$pool_name" >/dev/null

# Submit the reviewed CPU-only workflow to the pool selected by the connection receipt.
operation="submit CPU-only OSMO workflow"
submitted_at=$(date -u +%s)
submission_json=$(osmo workflow submit "$workflow" --format-type json --pool "$pool_name" \
  --set-string "workflow_name=$workflow_name" \
  --set-string "backend_name=$backend_name" \
  --set-string "pool_name=$pool_name" \
  --set-string "image=$image")
workflow_id=$(jq -r '.id // .workflow_id // .uuid // empty' <<< "$submission_json")
[[ -n "$workflow_id" ]] || fatal "OSMO submission response did not contain a workflow ID"

# Wait for completion and verify the remote result reports success with no GPU requested or present.
operation="wait for CPU-only OSMO workflow"
hil_wait_for_workflow "$workflow_id" 600

operation="validate CPU-only workflow result"
workflow_logs=$(osmo workflow logs "$workflow_id")
cpu_result=$(grep -o 'HIL_CPU_RESULT={[^}]*}' <<< "$workflow_logs" | tail -1 | cut -d= -f2-)
jq -e --arg workflow "$workflow_name" --arg backend "$backend_name" --arg pool "$pool_name" '
  .workflow_name == $workflow and .backend_name == $backend and .pool_name == $pool and
  .status == "passed" and .gpu_requested == 0 and .gpu_device_present == false
' <<< "$cpu_result" >/dev/null || fatal "CPU workflow result does not match the connected backend and zero-GPU contract"

# Find the submitted Pod on the owned node and verify its digest, exit status, and restricted permissions.
operation="verify CPU workflow ran on the owned local node"
matched_node=""
matched_pod=""
pods_output=$(kube_kubectl "$kubeconfig" "$context" get pods -n "$workflow_namespace" -o name)
while IFS= read -r pod; do
  [[ -n "$pod" ]] || continue
  pod_created=$(kube_kubectl "$kubeconfig" "$context" get "$pod" -n "$workflow_namespace" \
    -o jsonpath='{.metadata.creationTimestamp}')
  (( $(date -u -d "$pod_created" +%s) >= submitted_at )) || continue
  if kube_kubectl "$kubeconfig" "$context" logs "$pod" -n "$workflow_namespace" \
  --all-containers=true | grep -Fq "\"workflow_name\":\"${workflow_name}\""; then
    matched_node=$(kube_kubectl "$kubeconfig" "$context" get "$pod" -n "$workflow_namespace" \
      -o jsonpath='{.spec.nodeName}')
    matched_pod="$pod"
    break
  fi
done <<< "$pods_output"
[[ "$matched_node" == "$node_name" ]] || fatal "CPU workflow did not complete on the owned local node $node_name"
pod_json=$(kube_kubectl "$kubeconfig" "$context" get "$matched_pod" -n "$workflow_namespace" -o json)
jq -e --arg image "$image" '
  .status.phase == "Succeeded" and
  all(.status.containerStatuses[]?, .status.initContainerStatuses[]?;
    (.state.terminated.exitCode // 1) == 0 and
    ((.imageID // "") | endswith("@" + ($image | split("@")[-1]))) and
    .ready == false)
' <<< "$pod_json" >/dev/null || fatal "CPU workflow Pod did not complete with the approved image digest"
pod_service_account=$(jq -r '.spec.serviceAccountName // "default"' <<< "$pod_json")
[[ "$(kube_kubectl "$kubeconfig" "$context" auth can-i '*' '*' \
  --as "system:serviceaccount:${workflow_namespace}:${pod_service_account}" --all-namespaces)" != "yes" ]] || \
  fatal "CPU workflow service account has unrestricted cluster permissions"

# Report the successful scheduling proof and point to the next no-command safety check.
trap - ERR
section "Deployment Summary"
print_kv "Milestone" "validated: CPU scheduling"
print_kv "Workflow" "$workflow_name"
print_kv "Workflow ID" "$workflow_id"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Node" "$node_name"
print_kv "Service URL" "$service_url"
print_kv "GPU" "0"
print_kv "Status" "passed"
print_kv "Next" "$SCRIPT_DIR/04-run-no-command-check.sh --connection-file $connection_file"
info "CPU-only scheduling proof passed"
