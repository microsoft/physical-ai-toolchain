#!/usr/bin/env bash
# Update Helm chart versions and SHA256 hashes in defaults.conf
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
# shellcheck source=scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=infrastructure/setup/defaults.conf
source "$REPO_ROOT/infrastructure/setup/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Pull the latest version of each pinned Helm chart, compute its SHA256 hash,
and update infrastructure/setup/defaults.conf in-place.

OPTIONS:
    -h, --help               Show this help message
    --dry-run                Show what would change without writing
    --config-preview         Print current configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --dry-run
EOF
}

# Defaults
dry_run=false
config_preview=false
defaults_conf="$REPO_ROOT/infrastructure/setup/defaults.conf"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --dry-run)         dry_run=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1" ;;
  esac
done

require_tools helm sha256sum sed jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

section "Current Chart Configuration"
print_kv "GPU Operator Version"  "$GPU_OPERATOR_VERSION"
print_kv "GPU Operator SHA256"   "$GPU_OPERATOR_CHART_SHA256"
print_kv "KAI Scheduler Version" "$KAI_SCHEDULER_VERSION"
print_kv "KAI Scheduler SHA256"  "$KAI_SCHEDULER_CHART_SHA256"
print_kv "OSMO Chart Version"    "$OSMO_CHART_VERSION"
print_kv "OSMO Chart SHA256"     "$OSMO_CHART_SHA256"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi

#------------------------------------------------------------------------------
# Helper Functions
#------------------------------------------------------------------------------

update_default() {
  local var_name="$1" new_value="$2"
  sed -i "/^${var_name}=/s/:-[^}]*/:-${new_value}/" "$defaults_conf"
}

ensure_v_prefix() {
  local version="$1"
  if [[ "$version" != v* ]]; then echo "v${version}"; else echo "$version"; fi
}

strip_v_prefix() {
  local version="$1"
  echo "${version#v}"
}

pull_chart_sha() {
  local chart_ref="$1" version="$2" dest="$3"
  mkdir -p "$dest"
  helm pull "$chart_ref" --version "$version" --destination "$dest" || \
    fatal "helm pull failed for $chart_ref $version"
  local tgz
  tgz=$(find "$dest" -maxdepth 1 -name '*.tgz' -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
  [[ -n "$tgz" ]] || fatal "No .tgz found in $dest after helm pull"
  sha256sum "$tgz" | awk '{print $1}'
}

#------------------------------------------------------------------------------
# Main Logic
#------------------------------------------------------------------------------

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
updated=0

# --- GPU Operator ---
section "GPU Operator"
helm repo add nvidia "$HELM_REPO_GPU_OPERATOR" --force-update > /dev/null 2>&1
helm repo update nvidia > /dev/null 2>&1
gpu_latest_raw=$(helm search repo nvidia/gpu-operator --versions -o json | jq -r '.[0].version // empty')
[[ -n "$gpu_latest_raw" ]] || fatal "Failed to query latest GPU Operator version"
gpu_latest=$(ensure_v_prefix "$gpu_latest_raw")
gpu_sha=$(pull_chart_sha "nvidia/gpu-operator" "$gpu_latest_raw" "$tmpdir/gpu-operator")

print_kv "Current Version" "$GPU_OPERATOR_VERSION"
print_kv "Latest Version"  "$gpu_latest"
print_kv "New SHA256"       "$gpu_sha"

if [[ "$gpu_latest" != "$GPU_OPERATOR_VERSION" || "$gpu_sha" != "$GPU_OPERATOR_CHART_SHA256" ]]; then
  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] Would update GPU_OPERATOR_VERSION to $gpu_latest"
    info "[dry-run] Would update GPU_OPERATOR_CHART_SHA256 to $gpu_sha"
  else
    update_default "GPU_OPERATOR_VERSION" "$gpu_latest"
    update_default "GPU_OPERATOR_CHART_SHA256" "$gpu_sha"
    info "Updated GPU Operator to $gpu_latest ($gpu_sha)"
  fi
  updated=$((updated + 1))
else
  info "GPU Operator is up to date"
fi

# --- KAI Scheduler ---
section "KAI Scheduler"
kai_latest_raw=$(helm show chart oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler 2>/dev/null \
  | grep '^version:' | awk '{print $2}')
[[ -n "$kai_latest_raw" ]] || fatal "Failed to query latest KAI Scheduler version"
kai_latest=$(ensure_v_prefix "$kai_latest_raw")
kai_sha=$(pull_chart_sha "oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler" "$kai_latest_raw" "$tmpdir/kai-scheduler")

print_kv "Current Version" "$KAI_SCHEDULER_VERSION"
print_kv "Latest Version"  "$kai_latest"
print_kv "New SHA256"       "$kai_sha"

if [[ "$kai_latest" != "$KAI_SCHEDULER_VERSION" || "$kai_sha" != "$KAI_SCHEDULER_CHART_SHA256" ]]; then
  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] Would update KAI_SCHEDULER_VERSION to $kai_latest"
    info "[dry-run] Would update KAI_SCHEDULER_CHART_SHA256 to $kai_sha"
  else
    update_default "KAI_SCHEDULER_VERSION" "$kai_latest"
    update_default "KAI_SCHEDULER_CHART_SHA256" "$kai_sha"
    info "Updated KAI Scheduler to $kai_latest ($kai_sha)"
  fi
  updated=$((updated + 1))
else
  info "KAI Scheduler is up to date"
fi

# --- OSMO Backend Operator ---
section "OSMO Backend Operator"
helm repo add osmo "$HELM_REPO_OSMO" --force-update > /dev/null 2>&1
helm repo update osmo > /dev/null 2>&1
osmo_latest_raw=$(helm search repo osmo/backend-operator --versions -o json | jq -r '.[0].version // empty')
[[ -n "$osmo_latest_raw" ]] || fatal "Failed to query latest OSMO Backend Operator version"
osmo_latest=$(strip_v_prefix "$osmo_latest_raw")
osmo_sha=$(pull_chart_sha "osmo/backend-operator" "$osmo_latest_raw" "$tmpdir/osmo-backend")

print_kv "Current Version" "$OSMO_CHART_VERSION"
print_kv "Latest Version"  "$osmo_latest"
print_kv "New SHA256"       "$osmo_sha"

if [[ "$osmo_latest" != "$OSMO_CHART_VERSION" || "$osmo_sha" != "$OSMO_CHART_SHA256" ]]; then
  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] Would update OSMO_CHART_VERSION to $osmo_latest"
    info "[dry-run] Would update OSMO_CHART_SHA256 to $osmo_sha"
  else
    update_default "OSMO_CHART_VERSION" "$osmo_latest"
    update_default "OSMO_CHART_SHA256" "$osmo_sha"
    info "Updated OSMO Backend Operator to $osmo_latest ($osmo_sha)"
  fi
  updated=$((updated + 1))
else
  info "OSMO Backend Operator is up to date"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Charts Checked" "3"
print_kv "Charts Updated" "$updated"
print_kv "Dry Run"        "$dry_run"
print_kv "Defaults File"  "$defaults_conf"
