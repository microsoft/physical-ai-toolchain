#!/usr/bin/env bash
# Shared helpers for sdg.sh. Source only.

SDG_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CDF_DIR="$(cd -- "$SDG_SCRIPTS_DIR/.." && pwd)"
REPO_ROOT="$(cd -- "$CDF_DIR/.." && pwd)"
SDG_ENV_FILE="${SDG_ENV_FILE:-$CDF_DIR/configs/sdg.env}"

# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

DRY_RUN="${DRY_RUN:-0}"

# Variables from the shell win over the env file.
sdg_load_env() {
  [[ -f "$SDG_ENV_FILE" ]] || fatal "Missing $SDG_ENV_FILE (copy configs/sdg.env.example)"
  local line key
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    [[ -n "${!key:-}" ]] && continue
    # Empty values stay unset so the SDG repo's own defaults apply.
    [[ -n "${BASH_REMATCH[2]}" ]] || continue
    export "$key=${BASH_REMATCH[2]}"
  done < "$SDG_ENV_FILE"

  SDG_WORKFLOW_DIR="${SDG_WORKFLOW_DIR:-}"
  [[ -n "$SDG_WORKFLOW_DIR" ]] || fatal "SDG_WORKFLOW_DIR is not set in $SDG_ENV_FILE"
  SDG_WORKFLOW_DIR="$(cd -- "$SDG_WORKFLOW_DIR" 2>/dev/null && pwd)" \
    || fatal "SDG_WORKFLOW_DIR does not exist: ${SDG_WORKFLOW_DIR}"
  [[ -f "$SDG_WORKFLOW_DIR/Makefile" && -f "$SDG_WORKFLOW_DIR/scripts/setup_airflow_secrets.sh" ]] \
    || fatal "Not an sdg-workflow checkout: $SDG_WORKFLOW_DIR"
  export SDG_WORKFLOW_DIR
}

# Run a command, or only print it with --dry-run.
sdg_run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY-RUN] %s\n' "$*"
    return 0
  fi
  "$@"
}

sdg_make() {
  sdg_run make -C "$SDG_WORKFLOW_DIR" "$@"
}

sdg_preflight() {
  local failed=0 tool
  for tool in docker kubectl helm make python3 curl; do
    if command -v "$tool" >/dev/null 2>&1; then
      info "found $tool"
    else
      error "missing required tool: $tool"
      failed=1
    fi
  done
  (( failed == 0 )) || fatal "Install the missing tools and retry"

  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || fatal "Python 3.11 or newer is required"

  [[ -n "${NGC_API_KEY:-}" ]] || fatal "NGC_API_KEY is required"
  [[ -n "${HF_TOKEN:-}" ]] || fatal "HF_TOKEN is required"
  [[ -n "${KUBECONFIG:-}" && -f "${KUBECONFIG}" ]] || fatal "KUBECONFIG must point to an existing file"

  if [[ "$DRY_RUN" == "1" ]]; then
    info "dry run: skipping cluster connectivity checks"
    return 0
  fi
  kubectl cluster-info >/dev/null 2>&1 || fatal "Cannot reach the cluster using $KUBECONFIG"
  if ! kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}' | grep -q '[1-9]'; then
    warn "No allocatable nvidia.com/gpu found on any node"
  fi
  info "preflight passed"
}

# Call the Airflow REST API with a fresh JWT (long pipelines outlive a single token).
# Usage: airflow_api METHOD PATH [JSON_BODY]; prints the response body.
airflow_api() {
  local method="$1" path="$2" body="${3:-}" url header rc=0
  url="${AIRFLOW_URL:-http://127.0.0.1:8080}"
  header="$(umask 077; mktemp)"
  AF_USER="${AIRFLOW_USERNAME:-admin}" \
  AF_PASS="${AIRFLOW_PASSWORD:-admin}" \
  python3 -c 'import json,os; print(json.dumps({"username":os.environ["AF_USER"],"password":os.environ["AF_PASS"]}))' \
    | curl -fsS -X POST "$url/auth/token" \
            -H 'Content-Type: application/json' --data-binary @- \
    | python3 -c 'import json,sys; print("Authorization: Bearer " + json.load(sys.stdin)["access_token"])' \
    > "$header" \
    || { rm -f "$header"; fatal "Authentication failed at $url"; }
  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" "$url$path" -H @"$header" -H 'Content-Type: application/json' -d "$body" || rc=$?
  else
    curl -fsS -X "$method" "$url$path" -H @"$header" || rc=$?
  fi
  rm -f "$header"
  return "$rc"
}

# Trigger the SDG DAG with a payload file; prints the Airflow response body.
airflow_trigger_run() {
  local payload="$1" dag="${SDG_DAG_ID:-autonomous_vehicle_dataset_generation_dag_k8s}" body
  body="$(python3 - "$payload" <<'PY'
import datetime, json, sys
with open(sys.argv[1]) as f:
    payload = json.load(f)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
print(json.dumps({"logical_date": now, "conf": {"payload": payload}}))
PY
)"
  airflow_api POST "/api/v2/dags/$dag/dagRuns" "$body"
}

# Block until a DagRun finishes; fails unless it ends in success.
airflow_wait_run() {
  local run_id="$1" dag="${SDG_DAG_ID:-autonomous_vehicle_dataset_generation_dag_k8s}"
  local interval="${SDG_POLL_INTERVAL:-30}" timeout="${SDG_RUN_TIMEOUT:-21600}" waited=0 state
  while :; do
    state="$(airflow_api GET "/api/v2/dags/$dag/dagRuns/$run_id" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')" \
      || fatal "Could not read the state of DagRun $run_id"
    info "DagRun $run_id: $state"
    case "$state" in
      success) return 0 ;;
      failed) fatal "DagRun $run_id failed; inspect it in the Airflow UI" ;;
    esac
    (( waited < timeout )) || fatal "Timed out after ${timeout}s waiting for DagRun $run_id"
    sleep "$interval"
    waited=$((waited + interval))
  done
}

# Trigger a payload and wait for it to finish; prints the DagRun id.
airflow_run_and_wait() {
  local payload="$1" run_id
  run_id="$(airflow_trigger_run "$payload" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["dag_run_id"])')" \
    || fatal "Failed to trigger the DAG with $payload"
  info "Triggered DagRun $run_id" >&2
  airflow_wait_run "$run_id" >&2
  printf '%s\n' "$run_id"
}

odd_image() { printf '%s' "${ODD_IMAGE:-odd-component:latest}"; }

ODD_DIR="${ODD_DIR:-$CDF_DIR/ODD}"

odd_build() {
  sdg_run docker build -t "$(odd_image)" -f "$ODD_DIR/Dockerfile" "$ODD_DIR"
}

# Run the ODD gap analysis in its own container.
# Usage: odd_run INPUT_DIR OUTPUT_DIR; host paths are preserved in the output configs.
odd_run() {
  local input_dir="$1" output_dir="$2" image
  image="$(odd_image)"
  if [[ "$DRY_RUN" != "1" ]]; then
    [[ -d "$input_dir" ]] || fatal "ODD input directory not found: $input_dir"
    mkdir -p "$output_dir"
    docker image inspect "$image" >/dev/null 2>&1 || odd_build
  fi
  local args=(--input-dir /input --output-dir /output
    --threshold "${ODD_THRESHOLD:-0.8}" --host-video-dir "$input_dir")
  [[ "${ODD_MOCK_VLM:-0}" == "1" ]] && args+=(--mock-vlm)
  sdg_run docker run --rm --network host --user "$(id -u):$(id -g)" \
    -v "$input_dir:/input:ro" -v "$output_dir:/output" \
    -e "VLM_URL=${ODD_VLM_URL:-http://127.0.0.1:8010/v1}" \
    -e "VLM_MODEL=${ODD_VLM_MODEL:-nvidia/Cosmos-Reason2-2B}" \
    -e "VLM_INPUT=${ODD_VLM_INPUT:-video}" \
    "$image" "${args[@]}"
}

CARLA_DIR="${CARLA_DIR:-$CDF_DIR/carla-cosmos}"
CARLA_CONTAINER_DATA="/workspace/data"

# Generate with CARLA + Cosmos Transfer 2.5 multi-view from the ODD gaps (backend "carla").
# Cosmos multi-view needs 8 GPUs for 5-8 views and 4 for up to 4 views (CP size must be >= the view
# count and divide the 2B model's 16 attention heads). CARLA shares a Cosmos GPU (about 6 GB).
# Usage: carla_run ODD_OUTPUT_DIR OUTPUT_DIR [RUN_ID]
carla_run() {
  local odd_out="$1" out_dir="$2" run_id="${3:-}"
  local views="${CARLA_COSMOS_VIEWS:-front_wide,cross_left,cross_right,rear_left,rear_right,rear}"
  local gpus="${COSMOS_GPU_IDS:-0,1,2,3,4,5,6,7}" carla_gpu="${CARLA_GPU_ID:-7}"
  [[ -n "$run_id" ]] || run_id="$(date -u +%Y%m%dT%H%M%SZ)"
  run_id="${run_id//[^A-Za-z0-9._-]/-}"
  [[ -f "$CARLA_DIR/run_pipeline.py" ]] || fatal "CARLA workflow not found: $CARLA_DIR"

  local plan=(python3 "$SDG_SCRIPTS_DIR/sdg_odd.py" carla-plan --carla-dir "$CARLA_DIR"
    --views "$views" --gpu-ids "$gpus" --carla-gpu "$carla_gpu")
  if [[ "$DRY_RUN" == "1" ]]; then
    "${plan[@]}" || fatal "CARLA plan is invalid"
  else
    [[ -f "$odd_out/wfm_generation_input.json" ]] || fatal "ODD output not found: $odd_out/wfm_generation_input.json"
    "${plan[@]}" --odd-output "$odd_out" || fatal "CARLA plan is invalid"
  fi

    local run_dir="$CARLA_DIR/data/runs/$run_id"
  local container_run="$CARLA_CONTAINER_DATA/runs/$run_id"
  sdg_run mkdir -p "$run_dir/odd_input"
  sdg_run cp "$odd_out/wfm_generation_input.json" "$run_dir/odd_input/wfm_generation_input.json"

  local args=(python3 "$CARLA_DIR/run_pipeline.py"
    --input "$container_run/odd_input/wfm_generation_input.json" --run-dir "$container_run")

  # run_pipeline.py starts CARLA, runs every task in the pipeline container, then stops CARLA.
  local rc=0
  sdg_run env "CARLA_COSMOS_VIEWS=$views" "COSMOS_GPU_IDS=$gpus" "CARLA_GPU_ID=$carla_gpu" \
    "${args[@]}" || rc=$?
  # Keep whatever finished even when a task failed.
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY-RUN] python3 %s carla-collect --run-dir %s --out-dir %s\n' \
      "$SDG_SCRIPTS_DIR/sdg_odd.py" "$run_dir" "$out_dir"
  else
    python3 "$SDG_SCRIPTS_DIR/sdg_odd.py" carla-collect --run-dir "$run_dir" --out-dir "$out_dir" \
      || { (( rc != 0 )) || rc=1; }
  fi
  (( rc == 0 )) || fatal "CARLA backend failed (exit $rc); see $run_dir"
  info "CARLA backend complete: results in $out_dir"
}
