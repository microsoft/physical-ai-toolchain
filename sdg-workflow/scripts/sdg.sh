#!/usr/bin/env bash
set -euo pipefail

# Drive the external sdg-workflow (CDF Orchestration) repo from this toolchain.
# Configuration comes from configs/sdg.env; the SDG repo is never modified.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=sdg-lib.sh
source "$SCRIPT_DIR/sdg-lib.sh"

usage() {
  cat <<EOF
Usage: sdg.sh [--dry-run] <command> [args]

Commands:
  preflight          Check tools, secrets, kubeconfig and cluster access
  setup              Validate secrets and generate Helm secret values
  nfs                Install host NFS export and StorageClass
  build              Build and push the Airflow controller image
  install            Install or upgrade the SDG controller
  run                preflight + setup + nfs + build + install
  port-forward       Forward the Airflow UI to http://localhost:8080
  trigger <payload>  Trigger the DAG (SDG_DAG_ID) with a JSON payload file
  uninstall          Uninstall the controller

Options:
  --dry-run          Print the commands without executing them
  -h, --help         Show this help

Config file: $SDG_ENV_FILE (override with SDG_ENV_FILE)
EOF
}

trigger() {
  local payload="${1:-}"
  [[ -n "$payload" ]] || fatal "trigger requires a payload JSON file"
  [[ -f "$payload" ]] || [[ -f "$SDG_WORKFLOW_DIR/$payload" ]] || fatal "Payload not found: $payload"
  [[ -f "$payload" ]] || payload="$SDG_WORKFLOW_DIR/$payload"

  local url="${AIRFLOW_URL:-http://localhost:8080}"
  local dag="${SDG_DAG_ID:-autonomous_vehicle_dataset_generation_dag_k8s}"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY-RUN] POST %s/api/v2/dags/%s/dagRuns with %s\n' "$url" "$dag" "$payload"
    return 0
  fi

  local auth_header body
  auth_header="$(umask 077; mktemp)"
  trap 'rm -f "$auth_header"' RETURN

  curl -fsS -X POST "$url/auth/token" -H 'Content-Type: application/json' \
    -d "$(AF_USER="${AIRFLOW_USERNAME:-admin}" AF_PASS="${AIRFLOW_PASSWORD:-admin}" python3 -c \
      'import json,os; print(json.dumps({"username":os.environ["AF_USER"],"password":os.environ["AF_PASS"]}))')" \
    | python3 -c 'import json,sys; print("Authorization: Bearer " + json.load(sys.stdin)["access_token"])' \
    > "$auth_header" || fatal "Authentication failed at $url (is port-forward running?)"

  body="$(python3 - "$payload" <<'PY'
import datetime, json, sys
with open(sys.argv[1]) as f:
    payload = json.load(f)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
print(json.dumps({"logical_date": now, "conf": {"payload": payload}}))
PY
)"
  curl -fsS -X POST "$url/api/v2/dags/$dag/dagRuns" -H @"$auth_header" \
    -H 'Content-Type: application/json' -d "$body" \
    || fatal "Failed to trigger DAG $dag"
  echo
  info "Triggered $dag"
}

args=()
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) args+=("$a") ;;
  esac
done
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
    info "Done. Run: sdg.sh port-forward  (UI at http://localhost:8080)"
    ;;
  port-forward) sdg_make port-forward ;;
  trigger)      trigger "${args[1]:-}" ;;
  uninstall)    sdg_make uninstall ;;
  *)            usage; fatal "Unknown command: $cmd" ;;
esac
