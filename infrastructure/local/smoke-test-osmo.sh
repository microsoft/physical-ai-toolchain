#!/usr/bin/env bash
# Smoke-test the local OSMO platform: CPU and GPU tasks, task mounts, object-store inputs and outputs,
# local registry images, and rejection of ad-hoc host mounts.
# cspell:ignore tolower
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../data-pipeline/setup/defaults.conf
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"
# shellcheck source=../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"

WORKFLOWS_DIR="$SCRIPT_DIR/workflows"
SMOKE_IMAGE_REPOSITORY="physical-ai-local/registry-smoke-test"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Submit the local OSMO smoke workflow and check every result:

  - a CPU task sees $LOCAL_JOB_DATASETS_PATH read-only and $LOCAL_JOB_CACHE_PATH writable
  - a GPU task on $LOCAL_OSMO_GPU_PLATFORM reports its GPU
  - a file uploaded with 'osmo data upload' is a task input, and the task output
    downloads with 'osmo data download'
  - a task runs an image from the local registry ($LOCAL_REGISTRY_HOST)
  - a workflow that asks for an ad-hoc host mount is rejected

Run it after 02-deploy-osmo.sh; 01-deploy-platform-services.sh pushes the registry test image.

OPTIONS:
    -h, --help               Show this help message
    --keep-data              Keep the uploaded and produced objects
    --timeout SECONDS        Time allowed for the workflow (default: 900)
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --keep-data
EOF
}

keep_data=false
timeout=900
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --keep-data)        keep_data=true; shift ;;
    --timeout)          timeout="$2"; shift 2 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  fatal "Unknown option: $1" ;;
  esac
done

[[ "$timeout" =~ ^[0-9]+$ ]] || fatal "--timeout must be a number of seconds"
require_tools osmo curl jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local
export OSMO_CONFIG_FILE_DIR="$LOCAL_OSMO_CONFIG_DIR"
run_id="smoke-$(date -u +%Y%m%dT%H%M%SZ)"
data_prefix="s3://$LOCAL_OSMO_BUCKET/smoke/$run_id"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Service URL" "$PROFILE_OSMO_URL"
  print_kv "Pool" "$LOCAL_OSMO_POOL (platforms default, $LOCAL_OSMO_GPU_PLATFORM)"
  print_kv "Data Prefix" "$data_prefix"
  print_kv "Registry Image" "$LOCAL_REGISTRY_HOST/$SMOKE_IMAGE_REPOSITORY"
  print_kv "Keep Data" "$keep_data"
  exit 0
fi

# Run the registry image by digest, so the task proves which image K3s pulled.
registry_url="http://127.0.0.1:${LOCAL_REGISTRY_HOST##*:}"
smoke_digest=$(curl --fail --silent --head \
  -H 'Accept: application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json' \
  "$registry_url/v2/$SMOKE_IMAGE_REPOSITORY/manifests/latest" \
  | awk 'tolower($1) == "docker-content-digest:" {print $2}' | tr -d '\r') || true
[[ "$smoke_digest" == sha256:* ]] \
  || fatal "The local registry has no $SMOKE_IMAGE_REPOSITORY image; run 01-deploy-platform-services.sh with Docker available"
registry_image="$LOCAL_REGISTRY_HOST/$SMOKE_IMAGE_REPOSITORY@$smoke_digest"

work_dir=$(mktemp -d)
cleanup() {
  rm -rf "$work_dir"
  if [[ "$keep_data" != "true" ]]; then
    osmo data delete "$data_prefix/" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

#------------------------------------------------------------------------------
# Submit
#------------------------------------------------------------------------------
section "Submit"

mkdir -p "$work_dir/input" "$work_dir/output"
printf 'hello from %s\n' "$run_id" > "$work_dir/input/hello.txt"
osmo data upload "$data_prefix/input" "$work_dir/input/hello.txt" >/dev/null
info "Uploaded the test input to $data_prefix/input"

submission=$(osmo workflow submit "$WORKFLOWS_DIR/osmo-smoke.yaml" --pool "$LOCAL_OSMO_POOL" --format-type json \
  --set "gpu_platform=$LOCAL_OSMO_GPU_PLATFORM" "datasets_path=$LOCAL_JOB_DATASETS_PATH" \
  "cache_path=$LOCAL_JOB_CACHE_PATH" "input_url=$data_prefix/input" "output_url=$data_prefix/output" \
  "registry_image=$registry_image")
workflow=$(jq -r '.name' <<< "$submission")
[[ -n "$workflow" && "$workflow" != "null" ]] || fatal "OSMO did not accept the smoke workflow: $submission"
info "Submitted $workflow"

#------------------------------------------------------------------------------
# Wait
#------------------------------------------------------------------------------
section "Wait"

waited=0
status=""
while (( waited < timeout )); do
  status=$(osmo workflow query "$workflow" --format-type json | jq -r '.status')
  case "$status" in
    COMPLETED|FAILED*) break ;;
  esac
  sleep 10
  waited=$(( waited + 10 ))
done
if [[ "$status" != "COMPLETED" ]]; then
  osmo workflow query "$workflow" --format-type json \
    | jq -r '.groups[]?.tasks[]? | "\(.name): \(.status) \(.failure_message // "")"' >&2 || true
  fatal "Workflow $workflow ended as ${status:-unknown} after ${waited}s"
fi
info "Workflow $workflow completed in about ${waited}s"

#------------------------------------------------------------------------------
# Check Results
#------------------------------------------------------------------------------
section "Check Results"

logs=$(osmo workflow logs "$workflow")
result() {
  grep -o "$1_RESULT=.*" <<< "$logs" | head -n 1 | cut -d= -f2-
}
mounts_result=$(result MOUNTS)
gpu_result=$(result GPU)
data_result=$(result DATA)
registry_result=$(result REGISTRY)
[[ "$mounts_result" == "datasets-read-only,cache-writable,cache-env" ]] \
  || fatal "Task mounts check failed: ${mounts_result:-no result}"
[[ "$gpu_result" == NVIDIA* ]] || fatal "GPU check failed: ${gpu_result:-no result}"
[[ "$registry_result" == "pulled-from-local-registry" ]] || fatal "Registry image check failed"

expected=$(tr '[:lower:]' '[:upper:]' < "$work_dir/input/hello.txt")
[[ "$data_result" == "$expected" ]] || fatal "Task input check failed: ${data_result:-no result}"
osmo data download "$data_prefix/output" "$work_dir/output" >/dev/null
downloaded=$(find "$work_dir/output" -name hello-processed.txt -type f -exec cat {} \;)
[[ "$downloaded" == "$expected" ]] || fatal "Downloaded task output does not match the processed input"
info "Mounts, GPU, object-store input and output, and the registry image all passed"

rejection=$(osmo workflow submit "$WORKFLOWS_DIR/osmo-mount-rejected.yaml" --pool "$LOCAL_OSMO_POOL" \
  --format-type json 2>&1) && fatal "OSMO accepted a workflow with an ad-hoc host mount"
grep -q 'does not allow mount' <<< "$rejection" \
  || fatal "The host-mount workflow failed for another reason: $rejection"
info "OSMO rejected the ad-hoc host mount"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Smoke Test Summary"
print_kv "Workflow" "$workflow"
print_kv "Task Mounts" "$mounts_result"
print_kv "GPU" "$gpu_result"
print_kv "Object Store" "uploaded input, task output downloaded ($data_prefix)"
print_kv "Registry Image" "$registry_image"
print_kv "Host Mounts" "ad-hoc mount rejected"
print_kv "Test Data" "$([[ "$keep_data" == "true" ]] && echo "kept at $data_prefix" || echo removed)"
info "Local OSMO smoke test passed"
