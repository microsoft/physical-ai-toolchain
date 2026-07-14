#!/usr/bin/env bash
# Submit a CPU-only OSMO smoke workflow to the HiL pool.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit a digest-pinned CPU-only workflow to an isolated OSMO HiL pool profile.

OPTIONS:
    -h, --help                Show this help message
    --pool NAME               OSMO HiL pool (required)
    --service-url URL         Private OSMO URL (required)
    --osmo-config-dir PATH    Protected isolated OSMO profile directory (required)
    --workflow-name NAME      Workflow name (default: hil-cpu-smoke)
    --image IMAGE             Digest-pinned CPU image override
    --config-preview          Print configuration and exit

EXAMPLES:
    $(basename "$0") --pool hil-lab-01 --service-url http://10.0.5.7 \
      --osmo-config-dir /protected/osmo-hil-profile
EOF
}

pool=""
service_url=""
osmo_config_dir=""
workflow_name="hil-cpu-smoke"
image="alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1"
workflow="$REPO_ROOT/evaluation/hil/workflows/osmo/cpu-smoke.yaml"
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --pool)             pool="$2"; shift 2 ;;
    --service-url)      service_url="$2"; shift 2 ;;
    --osmo-config-dir)  osmo_config_dir="$2"; shift 2 ;;
    --workflow-name)    workflow_name="$2"; shift 2 ;;
    --image)            image="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$pool" ]] || fatal "--pool is required"
[[ -n "$service_url" ]] || fatal "--service-url is required"
[[ -n "$osmo_config_dir" ]] || fatal "--osmo-config-dir is required"
[[ "$service_url" =~ ^http://(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.) ]] || \
  fatal "CPU smoke requires a private RFC1918 HTTP endpoint"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || fatal "--image must use an immutable sha256 digest"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Pool" "$pool"
  print_kv "Service URL" "$service_url"
  print_kv "OSMO Config" "$osmo_config_dir"
  print_kv "Workflow" "$workflow_name"
  print_kv "Image" "$image"
  print_kv "GPU" "0"
  exit 0
fi

require_tools osmo stat
[[ -d "$osmo_config_dir" ]] || fatal "OSMO config directory not found: $osmo_config_dir"
permissions=$(stat -c '%a' "$osmo_config_dir" 2>/dev/null || stat -f '%Lp' "$osmo_config_dir")
(( (8#$permissions & 8#077) == 0 )) || fatal "OSMO config directory must not be accessible by group or other users"
export XDG_CONFIG_HOME="$osmo_config_dir"
osmo profile set pool "$pool" >/dev/null || \
  fatal "Isolated profile is not authenticated; run XDG_CONFIG_HOME=$osmo_config_dir osmo login $service_url"

section "Submit CPU Smoke Workflow"
if osmo workflow query "$workflow_name" --output json >/dev/null 2>&1; then
  fatal "Workflow name already exists: $workflow_name"
fi
osmo workflow submit "$workflow" \
  --set-string "workflow_name=$workflow_name" \
  --set-string "image=$image"
wait_for_osmo_workflow "$workflow_name" 600 5 || fatal "CPU smoke workflow did not pass"
workflow_logs=$(osmo workflow logs "$workflow_name")
cpu_result=$(grep -o 'HIL_CPU_RESULT={[^}]*}' <<< "$workflow_logs" | tail -1 | cut -d= -f2-)
jq -e --arg workflow_name "$workflow_name" '
  .workflow_name == $workflow_name and
  .status == "passed" and
  .gpu_requested == 0 and
  .gpu_device_present == false
' \
  <<< "$cpu_result" >/dev/null || fatal "CPU smoke logs did not contain the expected remote result"

section "Deployment Summary"
print_kv "Workflow" "$workflow_name"
print_kv "Pool" "$pool"
print_kv "Service URL" "$service_url"
print_kv "Image" "$image"
print_kv "GPU" "0"
print_kv "Remote Result" "verified"
print_kv "Status" "completed"
info "CPU smoke workflow submitted"
