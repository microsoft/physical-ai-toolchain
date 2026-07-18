#!/usr/bin/env bash
# Prove representative actions cannot cross the no-command boundary.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"

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

connection_file="${HIL_CONNECTION_FILE:-}"
workflow_name="hil-no-command-$(date -u +%Y%m%dt%H%M%S)-${RANDOM}${RANDOM}"
config="$REPO_ROOT/evaluation/hil/config/ur10e-no-command.json"
fixture="$REPO_ROOT/evaluation/hil/config/ur10e-observations.jsonl"
runner="$REPO_ROOT/evaluation/hil/no_command_runner.py"
workflow="$REPO_ROOT/evaluation/hil/workflows/osmo/hil-evaluation.yaml"
runner_sha256="47dd89ad0e49e57b4883dcd52fc99bcf320d52ec1f8a3a2d6e0b84654f446b28"
config_sha256="7fb89cfb6f423267802f908ee48110e4918e08c6e0e53f14f3b9141ecb6dd3c2"
fixture_sha256="952af3e1794165370193433e05122f523624bc7e4ed531d2feb973e13c64f12d"
workflow_sha256="a69f3fdbcb842f9b5648fe04bc1b73f606a0fd521acf99ea6f40c6186da9d311"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    --connection-file)   connection_file="$2"; shift 2 ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$connection_file" ]] || fatal "--connection-file is required"
[[ "$workflow_name" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || fatal "Invalid workflow name: $workflow_name"
image=$(jq -r '.policy.image // empty' "$config")
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || fatal "No-command image must use an immutable sha256 digest"

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

operation="validate successful OSMO connection"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: validated no-command safety"
  exit "$status"
}
trap report_failure ERR

require_tools base64 jq osmo
require_external_runtime_path "$connection_file"
require_protected_file "$connection_file"
jq -e '
  .schema_version == 1 and .kind == "physical-ai-hil-connection" and
  (.backend_name | test("^[a-z0-9-]+$")) and (.pool_name | test("^[a-z0-9-]+$")) and
  (.node_name | test("^[a-z0-9-]+$")) and (.workflow_namespace | test("^[a-z0-9-]+$")) and
  (.osmo_config_dir | type == "string" and length > 0)
' "$connection_file" >/dev/null || fatal "Connection receipt is invalid"
backend_name=$(jq -r '.backend_name' "$connection_file")
pool_name=$(jq -r '.pool_name' "$connection_file")
service_url=$(jq -r '.service_url' "$connection_file")
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

operation="validate package-free no-command assets"
for file in "$config" "$fixture" "$runner" "$workflow"; do
  [[ -f "$file" && ! -L "$file" ]] || fatal "Required no-command asset is unavailable: $file"
done
[[ "$(calculate_sha256 "$runner")" == "$runner_sha256" ]] || fatal "No-command runner differs from the reviewed asset"
[[ "$(calculate_sha256 "$config")" == "$config_sha256" ]] || fatal "No-command configuration differs from the reviewed asset"
[[ "$(calculate_sha256 "$fixture")" == "$fixture_sha256" ]] || fatal "No-command fixture differs from the reviewed asset"
[[ "$(calculate_sha256 "$workflow")" == "$workflow_sha256" ]] || fatal "No-command workflow differs from the reviewed asset"
jq -e '
  (keys | sort) == (["execution", "kind", "observations", "policy", "robot", "safety", "schema_version"] | sort) and
  .schema_version == 1 and .kind == "ur10e-no-command-dry-run" and
  .robot.command_transport == "none" and .robot.command_endpoint == null and
  .robot.device_paths == [] and .robot.robot_network_cidrs == [] and
  .safety.allow_motion == false and .safety.allow_command_transport == false and
  .safety.require_negative_command_probe == true
' "$config" >/dev/null || fatal "No-command configuration contains a command or motion capability"

runner_b64=$(base64 < "$runner" | tr -d '\n')
config_b64=$(base64 < "$config" | tr -d '\n')
observations_b64=$(base64 < "$fixture" | tr -d '\n')

operation="submit no-command OSMO workflow"
submitted_at=$(date -u +%s)
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

operation="wait for no-command OSMO workflow"
hil_wait_for_workflow "$workflow_id" 600

operation="validate no-command workflow result"
workflow_logs=$(osmo workflow logs "$workflow_id")
remote_result=$(grep -o 'HIL_NO_COMMAND_RESULT={[^}]*}' <<< "$workflow_logs" | tail -1 | cut -d= -f2-)
jq -e --arg workflow "$workflow_name" --arg backend "$backend_name" --arg pool "$pool_name" '
  .workflow_name == $workflow and .backend_name == $backend and .pool_name == $pool and
  .status == "passed" and .command_transport == "none" and .proposed_actions > 0 and
  .applied_actions == 0 and .negative_command_probe == "passed" and
  .rejection_code == "NO_COMMAND_TRANSPORT"
' <<< "$remote_result" >/dev/null || fatal "No-command result does not prove the connected zero-action boundary"

operation="verify no-command workflow ran on the owned local node"
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
[[ "$matched_node" == "$node_name" ]] || fatal "No-command workflow did not complete on the owned local node $node_name"
pod_json=$(kube_kubectl "$kubeconfig" "$context" get "$matched_pod" -n "$workflow_namespace" -o json)
jq -e --arg image "$image" '
  .status.phase == "Succeeded" and
  all(.status.containerStatuses[]?, .status.initContainerStatuses[]?;
    (.state.terminated.exitCode // 1) == 0 and
    ((.imageID // "") | endswith("@" + ($image | split("@")[-1]))) and
    .ready == false)
' <<< "$pod_json" >/dev/null || fatal "No-command Pod did not complete with the approved image digest"
pod_service_account=$(jq -r '.spec.serviceAccountName // "default"' <<< "$pod_json")
[[ "$(kube_kubectl "$kubeconfig" "$context" auth can-i '*' '*' \
  --as "system:serviceaccount:${workflow_namespace}:${pod_service_account}" --all-namespaces)" != "yes" ]] || \
  fatal "No-command workflow service account has unrestricted cluster permissions"

trap - ERR
section "Deployment Summary"
print_kv "Milestone" "validated: no-command safety"
print_kv "Workflow" "$workflow_name"
print_kv "Workflow ID" "$workflow_id"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Node" "$node_name"
print_kv "Service URL" "$service_url"
print_kv "Applied Actions" "0"
print_kv "Command Transport" "none"
print_kv "Status" "passed"
print_kv "Journey" "complete for CPU and no-command workloads"
print_kv "Physical Motion" "unsupported"
info "No-command safety proof passed"
