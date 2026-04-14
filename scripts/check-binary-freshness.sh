#!/usr/bin/env bash
## Check pinned binary hashes and Helm chart versions against upstream sources.
## Produces a SARIF report for GitHub Security tab integration.

set -o errexit -o nounset -o pipefail

# === Script Directory and Repository Root ===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "${SCRIPT_DIR}/.." && pwd))"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

# ============================================================
# Help
# ============================================================
show_help() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Check pinned binary hashes and Helm chart versions against upstream sources.
Produces a SARIF report for GitHub Security tab integration.

Options:
  --sarif-file FILE   Output SARIF file path (default: binary-freshness-results.sarif)
  --config-preview    Print configuration and exit without changes
  -h, --help          Show this help message and exit
EOF
}

# ============================================================
# Default Variables
# ============================================================
SARIF_FILE="binary-freshness-results.sarif"
CONFIG_PREVIEW=false
DEV_DEPS="infrastructure/setup/optional/isaac-sim-vm/scripts/install-dev-deps.sh"
THINLINC="infrastructure/setup/optional/isaac-sim-vm/scripts/install-thinlinc-silent.sh"
DEVCONTAINER=".devcontainer/devcontainer.json"
DEFAULTS_CONF="infrastructure/setup/defaults.conf"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null | sed 's|.*github\.com[:/]||;s|\.git$||')}"

# ============================================================
# Argument Parsing
# ============================================================
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sarif-file) SARIF_FILE="${2:?--sarif-file requires a value}"; shift 2 ;;
    --config-preview) CONFIG_PREVIEW=true; shift ;;
    -h|--help) show_help; exit 0 ;;
    *) fatal "Unknown option: $1" ;;
  esac
done

# ============================================================
# Required Tools
# ============================================================
require_tools curl sha256sum jq helm

# ============================================================
# Gather Configuration
# ============================================================
cd "${REPO_ROOT}"

# ============================================================
# Config Preview
# ============================================================
if [[ "${CONFIG_PREVIEW}" == "true" ]]; then
  section "Configuration Preview"
  print_kv "SARIF File" "${SARIF_FILE}"
  print_kv "Dev Deps Config" "${DEV_DEPS}"
  print_kv "ThinLinc Config" "${THINLINC}"
  print_kv "Devcontainer Config" "${DEVCONTAINER}"
  print_kv "Defaults Conf" "${DEFAULTS_CONF}"
  print_kv "GitHub Repository" "${GITHUB_REPOSITORY}"
  exit 0
fi

# ============================================================
# Functions
# ============================================================
extract_var() {
  local value
  value=$(grep -m1 "^${2}=" "$1" | sed 's/^[^=]*="//' | sed 's/"$//')
  if [[ "${value}" =~ ^\$\{[^:]+:-(.+)\}$ ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "${value}"
  fi
}

extract_json_var() {
  grep -oP "(?<=${2}=)[^ \\\"]*" "$1" | head -1
}

mismatch=0
sarif_results=()

add_sarif_result() {
  local rule_id="$1" message="$2" file="$3" level="$4"
  sarif_results+=("$(cat <<SARIF_ENTRY
{
  "ruleId": "${rule_id}",
  "level": "${level}",
  "message": { "text": "${message}" },
  "locations": [{
    "physicalLocation": {
      "artifactLocation": { "uri": "${file}", "uriBaseId": "%SRCROOT%" }
    }
  }]
}
SARIF_ENTRY
  )")
}

check_hash() {
  local name="$1" url="$2" expected="$3" file="$4"
  local tmpfile actual
  tmpfile=$(mktemp)

  info "Checking ${name}..."
  if ! curl -fsSL -o "${tmpfile}" "${url}"; then
    echo "::error file=${file}::Failed to download ${name} from ${url}"
    rm -f "${tmpfile}"
    mismatch=$((mismatch + 1))
    add_sarif_result "binary-freshness/download-failure" \
      "Failed to download ${name} from ${url}" "${file}" "error"
    return
  fi

  actual=$(sha256sum "${tmpfile}" | awk '{print $1}')
  rm -f "${tmpfile}"

  if [[ "${actual}" != "${expected}" ]]; then
    echo "::warning file=${file}::Hash mismatch for ${name}: expected ${expected}, got ${actual}. The upstream binary has changed. Run scripts/update-chart-hashes.sh to update pinned hashes."
    mismatch=$((mismatch + 1))
    add_sarif_result "binary-freshness/hash-mismatch" \
      "Hash mismatch for ${name}: expected ${expected}, got ${actual}. The upstream binary has changed. Run scripts/update-chart-hashes.sh to update pinned hashes." \
      "${file}" "warning"
  else
    info "  ✓ ${name} hash matches"
  fi
}

check_helm_version() {
  local name="$1" pinned="$2" latest="$3"
  pinned="${pinned#v}"
  latest="${latest#v}"
  info "Checking ${name} (pinned: ${pinned}, latest: ${latest})..."
  if [[ "${pinned}" != "${latest}" ]]; then
    echo "::warning file=${DEFAULTS_CONF}::${name} pinned at ${pinned} but latest is ${latest}. Run scripts/update-chart-hashes.sh to update pinned hashes."
    mismatch=$((mismatch + 1))
    add_sarif_result "binary-freshness/version-drift" \
      "${name} pinned at ${pinned} but latest is ${latest}. Run scripts/update-chart-hashes.sh to update pinned hashes." \
      "${DEFAULTS_CONF}" "warning"
  else
    info "  ✓ ${name} version is current"
  fi
}

with_retry() {
  local max_attempts="$1"
  shift
  local attempt output
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    if output=$("$@") && [[ -n "${output}" ]]; then
      echo "${output}"
      return 0
    fi
    if ((attempt < max_attempts)); then
      warn "  Attempt ${attempt}/${max_attempts} failed, retrying in ${attempt}s..."
      sleep "${attempt}"
    fi
  done
  return 1
}

helm_repo_latest() {
  local repo_name="$1" repo_url="$2" chart="$3"
  helm repo add "${repo_name}" "${repo_url}" --force-update > /dev/null 2>&1
  helm repo update "${repo_name}" > /dev/null 2>&1
  helm search repo "${chart}" --versions -o json 2>/dev/null | jq -r '.[0].version // empty'
}

helm_oci_latest() {
  local chart="$1"
  helm show chart "${chart}" 2>/dev/null | grep '^version:' | awk '{print $2}'
}

# ============================================================
# Binary Hash Checks
# ============================================================
section "Binary Hash Freshness Check"

nodesource_hash=$(extract_var "${DEV_DEPS}" "NODESOURCE_GPG_SHA256")
uv_version=$(extract_var "${DEV_DEPS}" "UV_VERSION")
uv_hash=$(extract_var "${DEV_DEPS}" "UV_INSTALLER_SHA256")
microsoft_hash=$(extract_var "${DEV_DEPS}" "MICROSOFT_GPG_SHA256")
nvidia_hash=$(extract_var "${DEV_DEPS}" "NVIDIA_CTK_GPG_SHA256")
tl_version=$(extract_var "${THINLINC}" "TL_VERSION")
tl_hash=$(extract_var "${THINLINC}" "TL_SHA256")
tflint_version=$(extract_json_var "${DEVCONTAINER}" "TFLINT_VERSION")
tflint_hash=$(extract_json_var "${DEVCONTAINER}" "TFLINT_SHA256")
osmo_hash=$(extract_json_var "${DEVCONTAINER}" "OSMO_INSTALLER_SHA256")
ngc_hash=$(extract_json_var "${DEVCONTAINER}" "NGC_CLI_SHA256")

check_hash "NodeSource GPG Key" \
  "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key" \
  "${nodesource_hash}" "${DEV_DEPS}"

check_hash "uv Installer (v${uv_version})" \
  "https://astral.sh/uv/${uv_version}/install.sh" \
  "${uv_hash}" "${DEV_DEPS}"

# Microsoft GPG key is shared by Azure CLI and VS Code repository entries.
# MICROSOFT_GPG_SHA256 and VSCODE_GPG_SHA256 pin the same URL.
check_hash "Microsoft GPG Key" \
  "https://packages.microsoft.com/keys/microsoft.asc" \
  "${microsoft_hash}" "${DEV_DEPS}"

check_hash "NVIDIA Container Toolkit GPG Key" \
  "https://nvidia.github.io/libnvidia-container/gpgkey" \
  "${nvidia_hash}" "${DEV_DEPS}"

check_hash "ThinLinc Server (v${tl_version})" \
  "https://www.cendio.com/downloads/server/tl-${tl_version}-server.zip" \
  "${tl_hash}" "${THINLINC}"

check_hash "TFLint (${tflint_version})" \
  "https://github.com/terraform-linters/tflint/releases/download/${tflint_version}/tflint_linux_amd64.zip" \
  "${tflint_hash}" "${DEVCONTAINER}"

check_hash "OSMO Installer" \
  "https://raw.githubusercontent.com/NVIDIA/OSMO/refs/heads/main/install.sh" \
  "${osmo_hash}" "${DEVCONTAINER}"

check_hash "NGC CLI" \
  "https://ngc.nvidia.com/downloads/ngccli_linux.zip" \
  "${ngc_hash}" "${DEVCONTAINER}"

# ============================================================
# Helm Chart Version Checks
# ============================================================
section "Helm Chart Version Freshness"

gpu_operator_version=$(extract_var "${DEFAULTS_CONF}" "GPU_OPERATOR_VERSION")
kai_scheduler_version=$(extract_var "${DEFAULTS_CONF}" "KAI_SCHEDULER_VERSION")
osmo_chart_version=$(extract_var "${DEFAULTS_CONF}" "OSMO_CHART_VERSION")
helm_repo_gpu_operator=$(extract_var "${DEFAULTS_CONF}" "HELM_REPO_GPU_OPERATOR")
helm_repo_osmo=$(extract_var "${DEFAULTS_CONF}" "HELM_REPO_OSMO")

gpu_latest=$(with_retry 3 helm_repo_latest "nvidia" "${helm_repo_gpu_operator}" "nvidia/gpu-operator")
if [[ -n "${gpu_latest}" ]]; then
  check_helm_version "GPU Operator" "${gpu_operator_version}" "${gpu_latest}"
else
  echo "::warning::Failed to query GPU Operator chart versions after retries"
  mismatch=$((mismatch + 1))
  add_sarif_result "binary-freshness/lookup-failure" \
    "Failed to query GPU Operator chart versions from Helm repository after retries." \
    "${DEFAULTS_CONF}" "warning"
fi

kai_latest=$(with_retry 3 helm_oci_latest "oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler")
if [[ -n "${kai_latest}" ]]; then
  check_helm_version "KAI Scheduler" "${kai_scheduler_version}" "${kai_latest}"
else
  echo "::warning::Failed to query KAI Scheduler chart version from OCI registry after retries"
  mismatch=$((mismatch + 1))
  add_sarif_result "binary-freshness/lookup-failure" \
    "Failed to query KAI Scheduler chart version from OCI registry after retries." \
    "${DEFAULTS_CONF}" "warning"
fi

osmo_latest=$(with_retry 3 helm_repo_latest "osmo" "${helm_repo_osmo}" "osmo/backend-operator")
if [[ -n "${osmo_latest}" ]]; then
  check_helm_version "OSMO Operator" "${osmo_chart_version}" "${osmo_latest}"
else
  echo "::warning::Failed to query OSMO chart versions after retries"
  mismatch=$((mismatch + 1))
  add_sarif_result "binary-freshness/lookup-failure" \
    "Failed to query OSMO Operator chart versions from Helm repository after retries." \
    "${DEFAULTS_CONF}" "warning"
fi

# ============================================================
# SARIF Report
# ============================================================
section "SARIF Report"

results_json="[]"
if [[ ${#sarif_results[@]} -gt 0 ]]; then
  for entry in "${sarif_results[@]}"; do
    results_json=$(echo "${results_json}" | jq --argjson entry "${entry}" '. + [$entry]')
  done
fi

jq -n \
  --arg schema "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json" \
  --argjson results "${results_json}" \
  --arg repo "${GITHUB_REPOSITORY}" \
  '{
    "$schema": $schema,
    "version": "2.1.0",
    "runs": [{
      "tool": {
        "driver": {
          "name": "binary-freshness-check",
          "informationUri": ("https://github.com/" + $repo),
          "rules": [
            {
              "id": "binary-freshness/download-failure",
              "shortDescription": { "text": "Binary download failed" },
              "helpUri": ("https://github.com/" + $repo + "/blob/main/scripts/update-chart-hashes.sh")
            },
            {
              "id": "binary-freshness/hash-mismatch",
              "shortDescription": { "text": "Pinned hash does not match upstream" },
              "helpUri": ("https://github.com/" + $repo + "/blob/main/scripts/update-chart-hashes.sh")
            },
            {
              "id": "binary-freshness/version-drift",
              "shortDescription": { "text": "Pinned chart version differs from latest" },
              "helpUri": ("https://github.com/" + $repo + "/blob/main/scripts/update-chart-hashes.sh")
            },
            {
              "id": "binary-freshness/lookup-failure",
              "shortDescription": { "text": "Chart version lookup failed after retries" },
              "helpUri": ("https://github.com/" + $repo + "/blob/main/scripts/update-chart-hashes.sh")
            }
          ]
        }
      },
      "results": $results
    }]
  }' > "${SARIF_FILE}"

info "SARIF results written to ${SARIF_FILE} (${#sarif_results[@]} finding(s))"

# ============================================================
# Deployment Summary
# ============================================================
section "Deployment Summary"
print_kv "SARIF File" "${SARIF_FILE}"
print_kv "Mismatches" "${mismatch}"
print_kv "SARIF Findings" "${#sarif_results[@]}"

if [[ "${mismatch}" -gt 0 ]]; then
  echo "::warning::${mismatch} pinned hash(es) differ from upstream. Review warnings above and update the affected scripts."
else
  info "All pinned hashes match upstream."
fi
