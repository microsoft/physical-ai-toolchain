#!/usr/bin/env bash
set -euo pipefail

# Drive the external sdg-workflow (CDF Orchestration) repo from this toolchain.
# Configuration comes from configs/sdg.env; the SDG repo is never modified.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sdg-lib.sh
source "$SCRIPT_DIR/sdg-lib.sh"

usage() {
  cat <<EOF
Usage: sdg.sh [--dry-run] [--backend dag|carla] <command> [args]

Commands:
  preflight          Check tools, secrets, kubeconfig and cluster access
  setup              Validate secrets and generate Helm secret values
  nfs                Install host NFS export and StorageClass
  build              Build and push the Airflow controller image
  install            Install or upgrade the SDG controller
  run                preflight + setup + nfs + build + install
  port-forward       Forward the Airflow UI to http://127.0.0.1:8080
  trigger <payload>  Trigger the DAG (SDG_DAG_ID) with a JSON payload file
  odd-build          Build the ODD gap-analysis image (AVOPS/ODD)
  odd <in> <out>     Run the ODD container on a directory of videos
  pipeline <payload> Curation -> ODD gap analysis -> generation (one run per gap)
  carla <odd-dir> [out-dir]
                     Generate from an ODD output with CARLA + Cosmos Transfer multi-view
  uninstall          Uninstall the controller

Options:
  --dry-run          Print the commands without executing them
  --backend <name>   Generation backend for pipeline: dag (default, Cosmos3 via Airflow)
                     or carla (CARLA + Cosmos Transfer 2.5 multi-view, six views on eight GPUs)
  -h, --help         Show this help

Config file: $SDG_ENV_FILE (override with SDG_ENV_FILE)
EOF
}

trigger() {
  local payload="${1:-}"
  [[ -n "$payload" ]] || fatal "trigger requires a payload JSON file"
  [[ -f "$payload" ]] || [[ -f "$SDG_WORKFLOW_DIR/$payload" ]] || fatal "Payload not found: $payload"
  [[ -f "$payload" ]] || payload="$SDG_WORKFLOW_DIR/$payload"

  local url="${AIRFLOW_URL:-http://127.0.0.1:8080}"
  local dag="${SDG_DAG_ID:-autonomous_vehicle_dataset_generation_dag_k8s}"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY-RUN] POST %s/api/v2/dags/%s/dagRuns with %s\n' "$url" "$dag" "$payload"
    return 0
  fi

  airflow_trigger_run "$payload" || fatal "Failed to trigger DAG $dag"
  echo
  info "Triggered $dag"
}

# Curation -> ODD gap analysis -> generation, driving the unmodified SDG DAG:
#   1. DAG run with generation disabled (curation + CDS ingest)
#   2. ODD container on the curated clips
#   3. One DAG run per ODD gap, with the ODD base clip as the query video and a gap prompt
#      (backend dag), or CARLA scenarios + Cosmos Transfer multi-view per gap (backend carla)
pipeline() {
  local payload="${1:-}"
  [[ -n "$payload" ]] || fatal "pipeline requires a payload JSON file"
  [[ -f "$payload" ]] || [[ -f "$SDG_WORKFLOW_DIR/$payload" ]] || fatal "Payload not found: $payload"
  [[ -f "$payload" ]] || payload="$SDG_WORKFLOW_DIR/$payload"
  payload="$(cd -- "$(dirname -- "$payload")" && pwd)/$(basename -- "$payload")"

  local helper="$SCRIPT_DIR/sdg_odd.py" max_runs="${ODD_MAX_RUNS:-0}"
  local curation_run clips odd_out generated gen_payload count=0
  local work
  work="$(mktemp -d)"
  PIPELINE_WORK="$work"
  trap 'rm -rf "$PIPELINE_WORK"' EXIT

  info "Stage 1/3: curation"
  python3 "$helper" curation-payload --payload "$payload" --out "$work/curation.json"
  if [[ "$DRY_RUN" == "1" ]]; then
    sdg_run "airflow trigger+wait" "$work/curation.json"
    clips="<output_directory>/<curation-run-id>/split/clips"
    odd_out="<output_directory>/<curation-run-id>/odd"
    info "Stage 2/3: ODD gap analysis"
    odd_run "$clips" "$odd_out"
    info "Stage 3/3: generation ($BACKEND)"
    if [[ "$BACKEND" == "carla" ]]; then
      carla_run "$odd_out" "${odd_out%/odd}/carla" "curation-run-id"
    else
      sdg_run "airflow trigger+wait (per ODD gap)" "$helper" generation-payloads \
        --payload "$payload" --odd-output "$odd_out" --max-runs "$max_runs"
    fi
    return 0
  fi

  command -v docker >/dev/null 2>&1 || fatal "missing required tool: docker"
  curation_run="$(airflow_run_and_wait "$work/curation.json")"
  clips="$(python3 "$helper" clips-dir --payload "$payload" --run-id "$curation_run")" \
    || fatal "Cannot locate curated clips"
  [[ -d "$clips" ]] || fatal "Curated clips not found: $clips"
  clips="$(cd -- "$clips" && pwd)"
  odd_out="${clips%/split/clips}/odd"

  info "Stage 2/3: ODD gap analysis ($clips)"
  odd_run "$clips" "$odd_out"

  info "Stage 3/3: generation ($BACKEND)"
  if [[ "$BACKEND" == "carla" ]]; then
    carla_run "$odd_out" "${odd_out%/odd}/carla" "$curation_run"
    info "Pipeline complete: CARLA results in ${odd_out%/odd}/carla; ODD report in $odd_out"
    return 0
  fi
  generated="$(python3 "$helper" generation-payloads --payload "$payload" \
    --odd-output "$odd_out" --out-dir "$work/generation" --max-runs "$max_runs")" \
    || fatal "Could not derive generation payloads from $odd_out"
  if [[ -z "$generated" ]]; then
    info "ODD found no weather or time-of-day gaps to generate; nothing more to do"
    return 0
  fi
  while IFS= read -r gen_payload; do
    count=$((count + 1))
    info "Generation run $count: $(basename -- "$gen_payload")"
    airflow_run_and_wait "$gen_payload" >/dev/null
  done <<< "$generated"
  info "Pipeline complete: $count generation run(s); ODD report in $odd_out"
}

BACKEND="${SDG_BACKEND:-dag}"
args=()
while (( $# > 0 )); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    --backend)
      (( $# > 1 )) || fatal "--backend requires dag or carla"
      BACKEND="$2"; shift ;;
    --backend=*) BACKEND="${1#--backend=}" ;;
    *) args+=("$1") ;;
  esac
  shift
done
case "$BACKEND" in
  dag|carla) ;;
  *) fatal "Unknown backend: $BACKEND (use dag or carla)" ;;
esac
[[ ${#args[@]} -gt 0 ]] || { usage; exit 1; }

cmd="${args[0]}"
sdg_load_env

case "$cmd" in
  preflight)    sdg_preflight ;;
  setup)        sdg_preflight; sdg_make setup ;;
  nfs)          sdg_make install-nfs ;;
  build)        sdg_make build-sdg-controller ;;
  install)      sdg_make install-sdg-controller ;;
  run)
    sdg_preflight
    sdg_make setup
    sdg_make install-nfs
    sdg_make build-sdg-controller
    sdg_make install-sdg-controller
    info "Done. Run: sdg.sh port-forward  (UI at http://127.0.0.1:8080)"
    ;;
  port-forward) sdg_make port-forward ;;
  trigger)      trigger "${args[1]:-}" ;;
  odd-build)    odd_build ;;
  odd)
    [[ -n "${args[1]:-}" && -n "${args[2]:-}" ]] || fatal "odd requires <input-dir> <output-dir>"
    if [[ "$DRY_RUN" == "1" ]]; then
      odd_run "${args[1]}" "${args[2]}"
    else
      [[ -d "${args[1]}" ]] || fatal "Input directory not found: ${args[1]}"
      mkdir -p "${args[2]}"
      odd_run "$(cd -- "${args[1]}" && pwd)" "$(cd -- "${args[2]}" && pwd)"
    fi
    ;;
  pipeline)     pipeline "${args[1]:-}" ;;
  carla)
    [[ -n "${args[1]:-}" ]] || fatal "carla requires <odd-output-dir>"
    if [[ "$DRY_RUN" == "1" ]]; then
      carla_run "${args[1]}" "${args[2]:-${args[1]}/carla}"
    else
      [[ -d "${args[1]}" ]] || fatal "ODD output directory not found: ${args[1]}"
      odd_dir="$(cd -- "${args[1]}" && pwd)"
      carla_run "$odd_dir" "${args[2]:-$odd_dir/carla}"
    fi
    ;;
  uninstall)    sdg_make uninstall ;;
  *)            usage; fatal "Unknown command: $cmd" ;;
esac
