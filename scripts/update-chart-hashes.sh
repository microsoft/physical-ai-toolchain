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

require_tools helm sed jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

section "Current Chart Configuration"
print_kv "GPU Operator Version"  "$GPU_OPERATOR_VERSION"
print_kv "GPU Operator SHA256"   "$GPU_OPERATOR_CHART_SHA256"
print_kv "KAI Scheduler Version" "$KAI_SCHEDULER_VERSION"
print_kv "KAI Scheduler SHA256"  "$KAI_SCHEDULER_CHART_SHA256"
print_kv "OSMO Chart Version"    "$OSMO_CHART_VERSION"
print_kv "OSMO Service SHA256"   "$OSMO_SERVICE_CHART_SHA256"
print_kv "OSMO Backend SHA256"   "$OSMO_BACKEND_CHART_SHA256"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi

#------------------------------------------------------------------------------
# Helper Functions
#------------------------------------------------------------------------------

update_default() {
  local var_name="$1" new_value="$2"
  local tmp_file
  tmp_file="$(mktemp)"
  sed "/^${var_name}=/s/:-[^}]*/:-${new_value}/" "$defaults_conf" > "$tmp_file"
  mv "$tmp_file" "$defaults_conf"
}

ensure_v_prefix() {
  local version="$1"
  if [[ "$version" != v* ]]; then echo "v${version}"; else echo "$version"; fi
}

strip_v_prefix() {
  local version="$1"
  echo "${version#v}"
}

# Query the latest semver release tag from a ghcr.io OCI repository.
# Paginates through the tags list API and returns the highest v* tag.
oci_latest_version() {
  local repo="$1"
  local token token_url last_tag tags all_tags=""

  # These are GHCR registry API metadata calls (a short-lived pull token and the
  # tags list), not artifact downloads — there is nothing to checksum here. Chart
  # integrity is verified separately: pull_chart_sha computes the SHA256 that gets
  # pinned in defaults.conf, and the deploy script's pull_and_verify_chart checks
  # the downloaded chart against that pin.
  token_url="https://ghcr.io/token?service=ghcr.io&scope=repository:${repo}:pull"
  token=$(curl -sf "$token_url" | jq -r '.token // empty')
  [[ -n "$token" ]] || return 1

  last_tag=""
  while true; do
    local url="https://ghcr.io/v2/${repo}/tags/list?n=100"
    [[ -n "$last_tag" ]] && url="${url}&last=${last_tag}"
    tags=$(curl -sf -H "Authorization: Bearer $token" "$url" | jq -r '.tags[]? // empty') || break
    [[ -n "$tags" ]] || break
    all_tags="${all_tags}${tags}"$'\n'
    last_tag=$(echo "$tags" | tail -1)
    # Stop if we got fewer than 100 tags (last page)
    local count
    count=$(echo "$tags" | wc -l | tr -d ' ')
    [[ "$count" -ge 100 ]] || break
  done

  echo "$all_tags" | grep '^v[0-9]' | sort -V | tail -1
}

pull_chart_sha() {
  local chart_ref="$1" version="$2" dest="$3"
  mkdir -p "$dest"
  helm pull "$chart_ref" --version "$version" --destination "$dest" >&2 || \
    fatal "helm pull failed for $chart_ref $version"
  local tgz
  tgz=$(find_latest_chart_archive "$dest")
  [[ -n "$tgz" ]] || fatal "No .tgz found in $dest after helm pull"
  calculate_sha256 "$tgz"
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
kai_latest=$(oci_latest_version "nvidia/kai-scheduler/kai-scheduler")
[[ -n "$kai_latest" ]] || fatal "Failed to query latest KAI Scheduler version"
kai_sha=$(pull_chart_sha "oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler" "$kai_latest" "$tmpdir/kai-scheduler")

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

# --- OSMO Charts ---
section "OSMO Charts"
helm repo add osmo "$HELM_REPO_OSMO" --force-update > /dev/null 2>&1
helm repo update osmo > /dev/null 2>&1
osmo_service_latest_raw=$(helm search repo "osmo/${OSMO_SERVICE_CHART}" --versions -o json | jq -r '.[0].version // empty')
[[ -n "$osmo_service_latest_raw" ]] || fatal "Failed to query latest OSMO service chart version"
osmo_backend_latest_raw=$(helm search repo "osmo/${OSMO_BACKEND_CHART}" --versions -o json | jq -r '.[0].version // empty')
[[ -n "$osmo_backend_latest_raw" ]] || fatal "Failed to query latest OSMO backend chart version"

osmo_service_latest=$(strip_v_prefix "$osmo_service_latest_raw")
osmo_backend_latest=$(strip_v_prefix "$osmo_backend_latest_raw")
[[ "$osmo_service_latest" == "$osmo_backend_latest" ]] || \
  fatal "OSMO chart versions diverged: service=$osmo_service_latest backend=$osmo_backend_latest"

osmo_latest="$osmo_service_latest"
osmo_service_sha=$(pull_chart_sha "osmo/${OSMO_SERVICE_CHART}" "$osmo_service_latest_raw" "$tmpdir/osmo-service")
osmo_backend_sha=$(pull_chart_sha "osmo/${OSMO_BACKEND_CHART}" "$osmo_backend_latest_raw" "$tmpdir/osmo-backend")

print_kv "Current Version"        "$OSMO_CHART_VERSION"
print_kv "Latest Version"         "$osmo_latest"
print_kv "Service SHA256"         "$osmo_service_sha"
print_kv "Backend SHA256"         "$osmo_backend_sha"

if [[ "$osmo_latest" != "$OSMO_CHART_VERSION" || "$osmo_service_sha" != "$OSMO_SERVICE_CHART_SHA256" || "$osmo_backend_sha" != "$OSMO_BACKEND_CHART_SHA256" ]]; then
  if [[ "$dry_run" == "true" ]]; then
    info "[dry-run] Would update OSMO_CHART_VERSION to $osmo_latest"
    info "[dry-run] Would update OSMO_SERVICE_CHART_SHA256 to $osmo_service_sha"
    info "[dry-run] Would update OSMO_BACKEND_CHART_SHA256 to $osmo_backend_sha"
  else
    update_default "OSMO_CHART_VERSION" "$osmo_latest"
    update_default "OSMO_SERVICE_CHART_SHA256" "$osmo_service_sha"
    update_default "OSMO_BACKEND_CHART_SHA256" "$osmo_backend_sha"
    if [[ "$osmo_latest" != "$OSMO_CHART_VERSION" ]]; then
      info "Updated OSMO charts to $osmo_latest"
    else
      info "Updated OSMO chart hashes (version unchanged: $osmo_latest)"
    fi
  fi
  updated=$((updated + 1))
else
  info "OSMO charts are up to date"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Charts Checked" "3 (GPU Operator, KAI Scheduler, OSMO Service+Backend)"
print_kv "Charts Updated" "$updated"
print_kv "Dry Run"        "$dry_run"
print_kv "Defaults File"  "$defaults_conf"
