#!/usr/bin/env bash
# Run or submit the independently non-commanding UR10E-shaped HiL dry run.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Run the UR10E-shaped no-command evaluation locally or submit it to OSMO.
This script contains no physical-motion mode and no robot transport.

OPTIONS:
    -h, --help                Show this help message
    --mode MODE               local|osmo (default: local)
    --config PATH             No-command JSON configuration
    --output-dir PATH         Local artifact directory
    --workflow PATH           OSMO workflow template
    --workflow-name NAME      OSMO workflow name (default: ur10e-no-command)
    --pool NAME               OSMO HiL pool (required for osmo mode)
    --service-url URL         Private OSMO service URL (required for osmo mode)
    --osmo-config-dir PATH    Empty protected directory for isolated OSMO login
    --config-preview          Print configuration and exit

EXAMPLES:
  $(basename "$0") --mode local --output-dir "$HOME/.local/share/physical-ai-toolchain/results/hil"
    $(basename "$0") --mode osmo --pool hil-lab-01 --service-url http://10.0.5.7 \
      --osmo-config-dir /protected/osmo-hil-profile
EOF
}

mode="local"
config="$REPO_ROOT/evaluation/hil/config/ur10e-no-command.json"
output_dir="${HIL_OUTPUT_DIR:-$REPO_ROOT/evaluation/hil/results/ur10e-no-command}"
workflow="$REPO_ROOT/evaluation/hil/workflows/osmo/hil-evaluation.yaml"
workflow_name="ur10e-no-command"
pool=""
service_url=""
osmo_config_dir=""
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --mode)             mode="$2"; shift 2 ;;
    --config)           config="$2"; shift 2 ;;
    --output-dir)       output_dir="$2"; shift 2 ;;
    --workflow)         workflow="$2"; shift 2 ;;
    --workflow-name)    workflow_name="$2"; shift 2 ;;
    --pool)             pool="$2"; shift 2 ;;
    --service-url)      service_url="$2"; shift 2 ;;
    --osmo-config-dir)  osmo_config_dir="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

[[ "$mode" == "local" || "$mode" == "osmo" ]] || fatal "--mode must be local or osmo"
[[ -f "$config" ]] || fatal "HiL configuration not found: $config"
[[ -f "$workflow" ]] || fatal "OSMO workflow not found: $workflow"
image=$(jq -r '.policy.image // empty' "$config")
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || fatal "--image must use an immutable sha256 digest"
fixture=$(python3 - "$config" <<'PYTHON'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1]).resolve()
value = json.loads(path.read_text())
print(path.parent / value["observations"]["fixture"])
PYTHON
)
[[ -f "$fixture" ]] || fatal "Observation fixture not found: $fixture"

if [[ "$mode" == "osmo" ]]; then
  [[ -n "$pool" ]] || fatal "--pool is required for OSMO submission"
  [[ -n "$service_url" ]] || fatal "--service-url is required for OSMO submission"
  [[ -n "$osmo_config_dir" ]] || fatal "--osmo-config-dir is required for isolated OSMO submission"
  [[ "$service_url" =~ ^http://(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.) ]] || \
    fatal "OSMO HiL submission requires a private RFC1918 HTTP endpoint"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Mode" "$mode"
  print_kv "Configuration" "$config"
  print_kv "Fixture" "$fixture"
  print_kv "Output Directory" "$output_dir"
  print_kv "Workflow" "$workflow"
  print_kv "Workflow Name" "$workflow_name"
  print_kv "Pool" "${pool:-local}"
  print_kv "Service URL" "${service_url:-local}"
  print_kv "OSMO Config" "${osmo_config_dir:-not used}"
  print_kv "Image" "$image"
  print_kv "Motion" "impossible: no command transport exists"
  exit 0
fi

require_tools base64 python3
runner="$REPO_ROOT/evaluation/hil/no_command_runner.py"

if [[ "$mode" == "local" ]]; then
  section "Run No-Command HiL Evaluation"
  python3 "$runner" --config "$config" --output-dir "$output_dir"
  summary=$(<"$output_dir/summary.json")
  jq -e '.status == "passed" and .applied_actions == 0 and .negative_command_probe == "passed"' \
    <<< "$summary" >/dev/null || fatal "No-command evaluation did not pass its independent safety probe"

  section "Deployment Summary"
  print_kv "Mode" "local"
  print_kv "Status" "passed"
  print_kv "Proposed Actions" "$(jq -r .proposed_actions <<< "$summary")"
  print_kv "Applied Actions" "$(jq -r .applied_actions <<< "$summary")"
  print_kv "Command Boundary" "$(jq -r .negative_command_probe <<< "$summary")"
  print_kv "Artifacts" "$output_dir"
  info "No-command HiL evaluation complete"
  exit 0
fi

require_tools jq osmo
[[ -d "$osmo_config_dir" ]] || fatal "OSMO config directory not found: $osmo_config_dir"
permissions=$(stat -c '%a' "$osmo_config_dir" 2>/dev/null || stat -f '%Lp' "$osmo_config_dir")
(( (8#$permissions & 8#077) == 0 )) || fatal "OSMO config directory must not be accessible by group or other users"
export XDG_CONFIG_HOME="$osmo_config_dir"

section "Validate Isolated OSMO Profile"
osmo profile set pool "$pool" >/dev/null || \
  fatal "Isolated OSMO profile is not authenticated for $service_url; run XDG_CONFIG_HOME=$osmo_config_dir osmo login $service_url"

runner_b64=$(base64 < "$runner" | tr -d '\n')
config_b64=$(base64 < "$config" | tr -d '\n')
observations_b64=$(base64 < "$fixture" | tr -d '\n')

section "Submit No-Command HiL Workflow"
if osmo workflow query "$workflow_name" --output json >/dev/null 2>&1; then
  fatal "Workflow name already exists: $workflow_name"
fi
osmo workflow submit "$workflow" \
  --set-string "workflow_name=$workflow_name" \
  --set-string "image=$image" \
  --set-string "runner_b64=$runner_b64" \
  --set-string "config_b64=$config_b64" \
  --set-string "observations_b64=$observations_b64"
wait_for_osmo_workflow "$workflow_name" 600 5 || fatal "No-command HiL workflow did not pass"
workflow_logs=$(osmo workflow logs "$workflow_name")
remote_summary=$(grep -o 'HIL_NO_COMMAND_RESULT={[^}]*}' <<< "$workflow_logs" | tail -1 | cut -d= -f2-)
jq -e --arg workflow_name "$workflow_name" '
  .workflow_name == $workflow_name and
  .status == "passed" and
  .command_transport == "none" and
  .proposed_actions > 0 and
  .applied_actions == 0 and
  .negative_command_probe == "passed" and
  .rejection_code == "NO_COMMAND_TRANSPORT"
' <<< "$remote_summary" >/dev/null || fatal "Remote no-command logs did not contain the expected safety result"

unset runner_b64 config_b64 observations_b64
section "Deployment Summary"
print_kv "Mode" "osmo"
print_kv "Workflow" "$workflow_name"
print_kv "Pool" "$pool"
print_kv "Service URL" "$service_url"
print_kv "Image" "$image"
print_kv "Status" "completed"
print_kv "Remote Result" "verified"
print_kv "Motion" "impossible: no command transport exists"
info "No-command HiL workflow submitted"
