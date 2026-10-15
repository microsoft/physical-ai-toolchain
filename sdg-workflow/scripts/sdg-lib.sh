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
