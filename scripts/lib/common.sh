#!/usr/bin/env bash
# Shared functions for deployment and submission scripts
# Follows k3s/Docker/Homebrew conventions for user-facing scripts
# cspell:ignore readyz dockerconfigjson managedclusters tolower

# Source repo-root .env.local for local environment overrides (not committed to git)
_common_sh_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_env_local="$(cd "$_common_sh_dir/../.." 2>/dev/null && pwd)/.env.local"
if [[ -f "$_env_local" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$_env_local"
  set +a
fi
unset _common_sh_dir _env_local

# Shared container defaults for training and evaluation submission scripts.
# OCI images are pinned by immutable @sha256 digest for reproducible, tamper-evident
# pulls; the tag is retained for readability. Refresh digests with
# scripts/update-image-digests.sh.
DEFAULT_ISAAC_LAB_IMAGE="${DEFAULT_ISAAC_LAB_IMAGE:-nvcr.io/nvidia/isaac-lab:2.3.2@sha256:388dbc806f48359a964cb9f807feb226da95d0a107f470fdcad9780ea10fe6f2}"
DEFAULT_LEROBOT_TRAIN_IMAGE="${DEFAULT_LEROBOT_TRAIN_IMAGE:-pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime@sha256:eee11b3b3872a8c838e35ef48f08b2d5def2080902c7f666831310ca1a0ef2be}"
DEFAULT_LEROBOT_EVAL_IMAGE="${DEFAULT_LEROBOT_EVAL_IMAGE:-pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime@sha256:0a3b9fedefe1f61ac4d5a9de9015c0863db27ca0fde2d4e37e6268147980b726}"
DEFAULT_GROOT_IMAGE="${DEFAULT_GROOT_IMAGE:-pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel@sha256:0cf3402e946b7c384ba943ee05c90b4c5a4a05227923921f2b0918c011cfaf56}"
# isaac-lab tag, available to callers that need the tag without the digest.
_isaac_ref="${DEFAULT_ISAAC_LAB_IMAGE%@*}"
DEFAULT_ISAAC_LAB_IMAGE_VERSION="${DEFAULT_ISAAC_LAB_IMAGE_VERSION:-${_isaac_ref##*:}}"
unset _isaac_ref
export DEFAULT_ISAAC_LAB_IMAGE DEFAULT_ISAAC_LAB_IMAGE_VERSION
export DEFAULT_LEROBOT_TRAIN_IMAGE DEFAULT_LEROBOT_EVAL_IMAGE DEFAULT_GROOT_IMAGE

# Logging functions with color support (NO_COLOR standard: https://no-color.org)
if [[ -z "${NO_COLOR+x}" ]]; then
  info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
  warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
  error() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; }
else
  info()  { printf '[INFO]  %s\n' "$*"; }
  warn()  { printf '[WARN]  %s\n' "$*" >&2; }
  error() { printf '[ERROR] %s\n' "$*" >&2; }
fi
fatal() { error "$@"; exit 1; }

derive_azureml_environment_version_from_image() {
  local image="$1" tag_ref tag digest version

  tag_ref="${image%@*}"
  tag="${tag_ref##*:}"
  [[ "$tag_ref" == *:* && "$tag" != */* ]] || fatal "Image reference must include a tag: $image"

  if [[ "$image" == *@sha256:* ]]; then
    digest="${image##*@sha256:}"
    [[ "$digest" =~ ^[0-9A-Fa-f]{64}$ ]] || fatal "Image reference has an invalid sha256 digest: $image"
    digest="$(printf '%s' "$digest" | tr '[:upper:]' '[:lower:]')"
    version="${tag}-sha256-${digest}"
  else
    version="$tag"
  fi

  [[ "$version" =~ ^[A-Za-z0-9._-]+$ ]] || fatal "Derived AzureML environment version contains unsupported characters: $version"
  printf '%s\n' "$version"
}

register_azureml_environment() {
  local name="${1:?environment name required}" version="${2:?environment version required}"
  local image="${3:?image required}" rg="${4:?resource group required}"
  local ws="${5:?workspace name required}" sub="${6:?subscription id required}"
  local env_file existing_image
  local create_args=(ml environment create)
  local show_args=(ml environment show)

  env_file=$(mktemp)

  cat >"$env_file" <<EOF
\$schema: https://azuremlschemas.azureedge.net/latest/environment.schema.json
name: $name
version: $version
image: $image
EOF

  create_args+=(--file "$env_file" --name "$name" --version "$version" --resource-group "$rg" --workspace-name "$ws" --subscription "$sub")
  show_args+=(--name "$name" --version "$version" --resource-group "$rg" --workspace-name "$ws" --subscription "$sub")

  info "Publishing AzureML environment ${name}:${version}"
  if az "${create_args[@]}" >/dev/null 2>&1; then
    rm -f "$env_file"
    return
  fi

  rm -f "$env_file"
  existing_image=$(az "${show_args[@]}" --query image -o tsv 2>/dev/null || true)

  [[ -n "$existing_image" ]] || fatal "Environment ${name}:${version} registration failed, and no existing environment image could be verified"
  [[ "$existing_image" == "$image" ]] || fatal "Environment ${name}:${version} already uses image '$existing_image', expected '$image'"
  info "Environment ${name}:${version} already exists with matching image; continuing"
}

# Check for required tools
require_tools() {
  local missing=()
  for tool in "$@"; do
    command -v "$tool" &>/dev/null || missing+=("$tool")
  done
  [[ ${#missing[@]} -eq 0 ]] || fatal "Missing required tools: ${missing[*]}"
}

# Require a remote HuggingFace repo reference to be pinned to an immutable 40-hex commit
# SHA; refuse a mutable HEAD. Absolute on-disk paths (starting with /) are exempt because
# the workflow resolves them directly without a Hub download.
#   $1 repo_ref  repo id or local path
#   $2 revision  revision to validate
#   $3 arg_name  CLI flag name for the error message (default: "revision")
require_hf_pin() {
  local repo_ref="$1" revision="$2" arg_name="${3:-revision}"
  [[ "$repo_ref" == /* ]] && return 0
  [[ -z "$revision" ]] && fatal "$arg_name is required for remote repo '$repo_ref' (refusing a mutable HEAD)"
  [[ "$revision" =~ ^[0-9a-fA-F]{40}$ ]] || fatal "$arg_name '$revision' must be an immutable 40-hex commit SHA for remote repo '$repo_ref'"
}

find_latest_chart_archive() {
  local output_dir="$1"
  local latest="" chart_archive

  while IFS= read -r -d '' chart_archive; do
    if [[ -z "$latest" || "$chart_archive" -nt "$latest" ]]; then
      latest="$chart_archive"
    fi
  done < <(find "$output_dir" -maxdepth 1 -type f -name '*.tgz' -print0)

  echo "$latest"
}

calculate_sha256() {
  local file="$1"

  if command -v sha256sum &>/dev/null; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum &>/dev/null; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    fatal "Missing required tool: sha256sum or shasum"
  fi
}

# Activate local OSMO development CLI wrapper
activate_local_osmo() {
  local repo_root
  repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  OSMO_DEV_CLI="${repo_root}/infrastructure/setup/optional/osmo-dev.sh"

  if [[ ! -x "$OSMO_DEV_CLI" ]]; then
    fatal "osmo-dev.sh not found at $OSMO_DEV_CLI"
  fi

  info "Using local OSMO CLI: $OSMO_DEV_CLI"

  # shellcheck disable=SC2329  # exported via export -f for child shells
  osmo() { "$OSMO_DEV_CLI" "$@"; }
  export OSMO_DEV_CLI
  export -f osmo
}

# Package repo-relative paths into a zip, upload it to a unique OSMO data URI,
# and echo that URI for a workflow `url:` input to consume.
#
# The payload is delivered to the workflow pod as a downloaded object (via the
# pod's workload identity) rather than an inline base64 env var: a single env
# string is capped at 128 KiB (Linux MAX_ARG_STRLEN) and the payload exceeds
# that, which fails the container execve with E2BIG.
#
# All progress logs go to stderr; only the URI is printed to stdout.
# Usage: code_url=$(stage_and_upload_code <repo_root> <uri_base> <path>...)
stage_and_upload_code() {
  local repo_root="${1:?repo_root required}"
  local uri_base="${2:?uri_base required}"
  shift 2
  [[ $# -gt 0 ]] || { error "stage_and_upload_code: no paths to package"; return 1; }

  local tmp archive hash uri size extract manifest
  tmp="$(mktemp -d)"
  archive="$tmp/osmo-code.zip"

  if ! (cd "$repo_root" && zip -qr "$archive" "$@" \
    -x '**/__pycache__/*' \
    -x '*.pyc' \
    -x '*.pyo' \
    -x '**/.pytest_cache/*' \
    -x '**/.mypy_cache/*' \
    -x '**/*.egg-info/*' \
    -x '**/.git/*' \
    -x '**/node_modules/*' \
    -x '**/.venv/*' \
    -x '**/.tmp/*') >&2; then
    rm -rf "$tmp"
    error "Failed to build code archive"
    return 1
  fi

  if [[ ! -s "$archive" ]]; then
    rm -rf "$tmp"
    error "Code archive is empty"
    return 1
  fi

  size="$(wc -c < "$archive" | tr -d ' ')"

  # Content-addressed key over the archive *contents* (per-file sha + sorted
  # relative path), not the .zip itself: Info-ZIP records each entry's mtime, so
  # hashing the archive file would yield a new key for byte-identical code on
  # every submit. Re-reading the just-built archive hashes exactly what is
  # uploaded and reuses the exclude set applied above.
  extract="$tmp/extract"
  manifest="$tmp/manifest"
  mkdir -p "$extract"
  if ! unzip -qq "$archive" -d "$extract" >&2; then
    rm -rf "$tmp"
    error "Failed to extract code archive for content hashing"
    return 1
  fi
  ( cd "$extract" && find . -type f -print0 | LC_ALL=C sort -z \
    | while IFS= read -r -d '' f; do
        printf '%s  %s\n' "$(calculate_sha256 "$f")" "$f"
      done ) > "$manifest"
  if [[ ! -s "$manifest" ]]; then
    rm -rf "$tmp"
    error "Code archive contains no files to hash"
    return 1
  fi
  hash="$(calculate_sha256 "$manifest" | cut -c1-16)"
  uri="${uri_base}/${hash}"

  # Upload only when the content-addressed object is absent. The key is a pure
  # function of the code, so an existing object is byte-identical: skipping the
  # upload avoids overwriting an object a concurrent job may be downloading
  # (osmo data upload always overwrites) and saves re-uploading unchanged code.
  #
  # The list (check) and upload (use) are not atomic, so there is a TOCTOU
  # window. It is benign by construction: because the key is content-addressed,
  # two submitters that both observe "absent" upload byte-identical bytes to the
  # same key, and no workflow pod reads the object until *after* its submitter
  # has finished uploading and submitted the workflow. The worst case is a
  # redundant overwrite with identical content, never a corrupt or partial read.
  if osmo data list "$uri" "$tmp/listing" >&2 && grep -q "$hash" "$tmp/listing" 2>/dev/null; then
    info "Code archive already present at ${uri}; skipping upload" >&2
    rm -rf "$tmp"
    echo "$uri"
    return 0
  fi

  info "Uploading code archive (${size} bytes) to ${uri}" >&2
  if ! osmo data upload "$uri" "$archive" >&2; then
    rm -rf "$tmp"
    error "osmo data upload failed for ${uri}"
    return 1
  fi

  rm -rf "$tmp"
  echo "$uri"
}

wait_for_osmo_workflow() {
  local workflow_name="${1:?workflow name required}"
  local timeout_seconds="${2:-600}" poll_seconds="${3:-5}"
  local elapsed=0 status=""

  while (( elapsed < timeout_seconds )); do
    status=$(osmo workflow query "$workflow_name" --output json 2>/dev/null | \
      jq -r '.status // .state // empty' 2>/dev/null || true)
    case "$status" in
      COMPLETED|completed|Completed|SUCCEEDED|succeeded|Succeeded)
        info "OSMO workflow $workflow_name completed successfully"
        return 0
        ;;
      FAILED|failed|Failed|ERROR|error|Error)
        error "OSMO workflow $workflow_name failed"
        error "Inspect logs with: osmo workflow logs $workflow_name"
        return 1
        ;;
      CANCELLED|cancelled|Canceled|CANCELED|canceled)
        error "OSMO workflow $workflow_name was cancelled"
        return 1
        ;;
    esac
    sleep "$poll_seconds"
    elapsed=$((elapsed + poll_seconds))
  done

  error "OSMO workflow $workflow_name did not complete within ${timeout_seconds}s (last status: ${status:-unknown})"
  return 1
}

# Ensure Azure CLI extension is installed
require_az_extension() {
  local ext="${1:?extension name required}"
  if ! az extension show --name "$ext" &>/dev/null; then
    info "Installing Azure CLI extension '$ext'..."
    az extension add --name "$ext" --yes || fatal "Failed to install Azure CLI extension '$ext'"
  fi
}

# Pull a Helm chart and optionally verify its SHA256 hash.
# Usage: pull_and_verify_chart <chart_ref> <version> <expected_sha256> <output_dir>
#   chart_ref     — repo/chart name or oci:// URI
#   version       — exact chart version passed to helm pull
#   expected_sha256 — expected SHA256 digest; empty string skips verification
#   output_dir    — directory to store the downloaded .tgz
# Prints the path to the downloaded .tgz on stdout.
# Stdout is the return channel; send diagnostics to stderr.
pull_and_verify_chart() {
  local chart_ref="$1" version="$2" expected_sha="$3" output_dir="$4"
  mkdir -p "$output_dir"

  helm pull "$chart_ref" --version "$version" --destination "$output_dir" >&2 || {
    if [[ "$chart_ref" == oci://ghcr.io/* ]]; then
      error "helm pull failed for $chart_ref $version"
      error ""
      error "If the helm error above contains '403' or 'denied', a stale or expired credential may be cached in your Helm registry config."
      error "Most common fix (works for public packages such as kai-scheduler — restores the anonymous pull path):"
      error "  helm registry logout ghcr.io"
      exit 1
    else
      fatal "helm pull failed for $chart_ref $version"
    fi
  }

  local tgz
  tgz=$(find_latest_chart_archive "$output_dir")
  [[ -n "$tgz" ]] || fatal "No .tgz found in $output_dir after helm pull"

  if [[ -n "$expected_sha" ]]; then
    local actual_sha
    actual_sha=$(calculate_sha256 "$tgz")
    if [[ "$actual_sha" != "$expected_sha" ]]; then
      fatal "SHA256 mismatch for $tgz: expected=$expected_sha actual=$actual_sha. Run scripts/update-chart-hashes.sh to update pinned hashes."
    fi
    info "Chart hash verified: $tgz ($actual_sha)" >&2
  else
    warn "No expected hash provided for $chart_ref $version — skipping verification. Run scripts/update-chart-hashes.sh to generate and pin a hash."
  fi

  echo "$tgz"
}

# Read terraform outputs from state file
read_terraform_outputs() {
  local tf_dir="${1:?terraform directory required}"
  [[ -d "$tf_dir" ]] || fatal "Terraform directory not found: $tf_dir"
  [[ -f "$tf_dir/terraform.tfstate" ]] || fatal "terraform.tfstate not found in $tf_dir"
  (cd "$tf_dir" && terraform output -json) || fatal "Unable to read terraform outputs"
}

# Extract value from terraform JSON output
tf_get() {
  local json="${1:?json required}" key="${2:?key required}" default="${3:-}"
  local val
  val=$(echo "$json" | jq -r ".$key // empty")
  if [[ -n "$val" ]]; then
    echo "$val"
  elif [[ -n "$default" ]]; then
    echo "$default"
  fi
}

# Require a terraform output value (fatal if missing)
tf_require() {
  local json="${1:?json required}" key="${2:?key required}"
  local description
  description="${3:-$key}"
  local val
  val=$(tf_get "$json" "$key")
  [[ -n "$val" ]] || fatal "$description not found in terraform outputs"
  echo "$val"
}

# Run kubectl against an explicit kubeconfig and context.
kube_kubectl() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  shift 2
  command kubectl --kubeconfig "$kubeconfig" --context "$context" "$@"
}

# Run Helm against an explicit kubeconfig and context.
kube_helm() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  shift 2
  command helm --kubeconfig "$kubeconfig" --kube-context "$context" "$@"
}

kube_api_server() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  command kubectl config view --kubeconfig "$kubeconfig" --context "$context" --minify \
    -o jsonpath='{.clusters[0].cluster.server}'
}

kube_system_namespace_uid() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  kube_kubectl "$kubeconfig" "$context" get namespace kube-system -o jsonpath='{.metadata.uid}'
}

kube_cluster_identity() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local server namespace_uid
  server=$(kube_api_server "$kubeconfig" "$context")
  namespace_uid=$(kube_system_namespace_uid "$kubeconfig" "$context")
  printf '%s\n' "$(printf '%s\n%s\n' "$server" "$namespace_uid" | calculate_sha256 /dev/stdin)"
}

# Validate that a context is reachable and represents the declared cluster role.
# Usage: verify_kube_target <kubeconfig> <context> <aks|k3s>
verify_kube_target() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local role="${3:?cluster role required}" server namespace_uid node_json

  [[ -f "$kubeconfig" ]] || fatal "Kubeconfig not found: $kubeconfig"
  [[ "$role" == "aks" || "$role" == "k3s" ]] || fatal "Unsupported Kubernetes target role: $role"
  command kubectl config get-contexts "$context" --kubeconfig "$kubeconfig" -o name 2>/dev/null | grep -qx "$context" || \
    fatal "Context '$context' not found in $kubeconfig"

  server=$(kube_api_server "$kubeconfig" "$context")
  [[ -n "$server" ]] || fatal "Context '$context' has no API server"
  if ! kube_kubectl "$kubeconfig" "$context" get --raw=/readyz >/dev/null 2>&1; then
    error "Cannot connect to Kubernetes context '$context' at $server"
    error "For private clusters, connect through the configured VPN first"
    fatal "Cluster connectivity check failed"
  fi

  namespace_uid=$(kube_system_namespace_uid "$kubeconfig" "$context")
  [[ -n "$namespace_uid" ]] || fatal "Unable to resolve kube-system namespace UID for '$context'"
  node_json=$(kube_kubectl "$kubeconfig" "$context" get nodes -o json)

  if [[ "$role" == "aks" ]]; then
    jq -e '.items | length > 0 and all(.[]; (.spec.providerID // "") | startswith("azure://"))' \
      <<< "$node_json" >/dev/null || fatal "Context '$context' does not identify an AKS cluster"
  else
    jq -e '.items | length > 0 and all(.[]; (.status.nodeInfo.kubeletVersion // "") | contains("+k3s"))' \
      <<< "$node_json" >/dev/null || fatal "Context '$context' does not identify a K3s cluster"
  fi

  info "Verified $(printf '%s' "$role" | tr '[:lower:]' '[:upper:]') context '$context' at $server (kube-system UID: $namespace_uid)"
}

verify_distinct_kube_targets() {
  local first_kubeconfig="${1:?first kubeconfig required}" first_context="${2:?first context required}"
  local second_kubeconfig="${3:?second kubeconfig required}" second_context="${4:?second context required}"
  local first_identity second_identity

  first_identity=$(kube_cluster_identity "$first_kubeconfig" "$first_context")
  second_identity=$(kube_cluster_identity "$second_kubeconfig" "$second_context")
  [[ "$first_identity" != "$second_identity" ]] || \
    fatal "Contexts '$first_context' and '$second_context' identify the same Kubernetes cluster"
}

# Verify Terraform and Azure resolve the same expected AKS resource before credential writes.
verify_aks_resource_id() {
  local tf_output="${1:?terraform output required}" expected_id="${2:?expected AKS resource ID required}"
  local state_id live_id resource_group cluster normalized_expected

  state_id=$(tf_require "$tf_output" "aks_cluster.value.id" "AKS cluster resource ID")
  resource_group=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
  cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster")
  live_id=$(az aks show --resource-group "$resource_group" --name "$cluster" --query id -o tsv)
  normalized_expected=$(printf '%s' "$expected_id" | tr '[:upper:]' '[:lower:]')

  [[ "$(printf '%s' "$state_id" | tr '[:upper:]' '[:lower:]')" == "$normalized_expected" ]] || \
    fatal "Terraform AKS resource ID does not match the expected target"
  [[ "$(printf '%s' "$live_id" | tr '[:upper:]' '[:lower:]')" == "$normalized_expected" ]] || \
    fatal "Azure AKS resource ID does not match the expected target"
  info "Verified expected AKS resource ID: $live_id"
}

require_no_symlink_path() {
  local path="${1:?path required}" current component
  local path_components=()

  if [[ "$path" == /* ]]; then
    current="/"
    path="${path#/}"
  else
    current="$PWD"
  fi

  IFS='/' read -r -a path_components <<< "$path"
  for component in "${path_components[@]}"; do
    case "$component" in
      ""|.) continue ;;
      ..) current=$(dirname "$current") ;;
      *)
        current="${current%/}/$component"
        [[ ! -L "$current" ]] || fatal "Protected path must not contain symlinks: $current"
        ;;
    esac
  done
}

# Reject an existing context that points to a different API server before overwrite.
verify_existing_aks_kubeconfig() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local expected_id="${3:?expected AKS resource ID required}"
  local resource_group cluster aks_json expected_host configured_server configured_host

  [[ ! -L "$kubeconfig" ]] || fatal "Kubeconfig must not be a symlink: $kubeconfig"
  [[ -f "$kubeconfig" ]] || return 0
  command kubectl config get-contexts "$context" --kubeconfig "$kubeconfig" -o name 2>/dev/null | grep -qx "$context" || return 0

  resource_group=$(awk -F/ '{for (i = 1; i <= NF; i++) if (tolower($i) == "resourcegroups") {print $(i+1); exit}}' <<< "$expected_id")
  cluster=$(awk -F/ '{for (i = 1; i <= NF; i++) if (tolower($i) == "managedclusters") {print $(i+1); exit}}' <<< "$expected_id")
  aks_json=$(az aks show --resource-group "$resource_group" --name "$cluster" -o json)
  expected_host=$(jq -r '.privateFqdn // .fqdn // empty' <<< "$aks_json")
  configured_server=$(kube_api_server "$kubeconfig" "$context")
  configured_host="${configured_server#*://}"
  configured_host="${configured_host%%:*}"
  configured_host="${configured_host%/}"

  [[ -n "$expected_host" && \
    "$(printf '%s' "$configured_host" | tr '[:upper:]' '[:lower:]')" == \
    "$(printf '%s' "$expected_host" | tr '[:upper:]' '[:lower:]')" ]] || \
    fatal "Existing context '$context' points to $configured_server, not expected AKS resource $expected_id"
  info "Verified existing AKS context '$context' at $configured_server before credential refresh"
}

# Bind direct kubectl and Helm calls in the current script to one explicit target.
activate_kube_target() {
  KUBE_TARGET_KUBECONFIG="${1:?kubeconfig required}"
  KUBE_TARGET_CONTEXT="${2:?context required}"
  export KUBE_TARGET_KUBECONFIG KUBE_TARGET_CONTEXT

  # shellcheck disable=SC2329  # invoked by callers after target activation
  kubectl() {
    command kubectl --kubeconfig "$KUBE_TARGET_KUBECONFIG" --context "$KUBE_TARGET_CONTEXT" "$@"
  }
  # shellcheck disable=SC2329  # invoked by callers after target activation
  helm() {
    command helm --kubeconfig "$KUBE_TARGET_KUBECONFIG" --kube-context "$KUBE_TARGET_CONTEXT" "$@"
  }
}

# Connect to AKS using an isolated kubeconfig and explicit context.
# Usage: connect_aks <resource-group> <cluster-name> <kubeconfig> [context]
connect_aks() {
  local rg="${1:?resource group required}" name="${2:?cluster name required}"
  local kubeconfig="${3:?isolated kubeconfig required}" context="${4:-$name}"
  local kubeconfig_dir

  kubeconfig_dir=$(dirname "$kubeconfig")
  require_no_symlink_path "$kubeconfig"
  [[ ! -L "$kubeconfig" ]] || fatal "Kubeconfig must not be a symlink: $kubeconfig"
  [[ ! -L "$kubeconfig_dir" ]] || fatal "Kubeconfig directory must not be a symlink: $kubeconfig_dir"
  mkdir -p "$kubeconfig_dir"
  chmod 700 "$kubeconfig_dir"
  info "Writing AKS credentials for $name to isolated kubeconfig $kubeconfig..."
  KUBECONFIG="$kubeconfig" az aks get-credentials --resource-group "$rg" --name "$name" \
    --context "$context" --overwrite-existing >/dev/null
  chmod 600 "$kubeconfig"

  if command -v kubelogin >/dev/null 2>&1; then
    kubelogin convert-kubeconfig --kubeconfig "$kubeconfig" --context "$context" -l azurecli >/dev/null
  fi
  verify_kube_target "$kubeconfig" "$context" aks
  activate_kube_target "$kubeconfig" "$context"
}

# Verify kubectl can reach an explicitly selected cluster API server.
verify_cluster_connectivity() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  info "Verifying cluster connectivity for context $context..."
  kube_kubectl "$kubeconfig" "$context" cluster-info >/dev/null || fatal "Cluster connectivity check failed"
  info "Cluster connectivity verified"
}

# Ensure a namespace exists on an explicit cluster target.
ensure_namespace() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local ns="${3:?namespace required}"
  kube_kubectl "$kubeconfig" "$context" create namespace "$ns" --dry-run=client -o yaml | \
    kube_kubectl "$kubeconfig" "$context" apply -f - >/dev/null
}

# Auto-detect ACR name from terraform outputs
detect_acr_name() {
  local tf_output="${1:?terraform output required}"
  local acr_name
  acr_name=$(tf_get "$tf_output" "container_registry.value.name")
  [[ -n "$acr_name" ]] || fatal "--use-acr specified but container_registry output not found in terraform state"
  echo "$acr_name"
}

verify_acr_image_manifest() {
  local manifest="${1:?image manifest required}" expected_login_server="${2:?login server required}"
  local expected_version="${3:?image version required}" registry login_server version name repository digest live_login_server
  local actual_digest attributes

  [[ -f "$manifest" ]] || fatal "ACR image manifest not found: $manifest"
  jq -e '
    .schema_version == 1 and
    (.registry | type == "string" and length > 0) and
    (.login_server | type == "string" and length > 0) and
    (.image_version | type == "string" and length > 0) and
    (["agent", "backend-listener", "backend-worker", "client", "delayed-job-monitor", "init-container", "logger", "router", "service", "web-ui", "worker"] - (.images | keys) | length == 0) and
    all(.images[]; (.repository | type == "string" and length > 0) and (.digest | test("^sha256:[0-9a-f]{64}$")))
  ' "$manifest" >/dev/null || fatal "Invalid ACR image manifest: $manifest"

  registry=$(jq -r '.registry' "$manifest")
  login_server=$(jq -r '.login_server' "$manifest")
  version=$(jq -r '.image_version' "$manifest")
  [[ "$registry" == "${expected_login_server%%.*}" ]] || fatal "Image manifest registry does not match $expected_login_server"
  [[ "$login_server" == "$expected_login_server" ]] || fatal "Image manifest login server does not match $expected_login_server"
  [[ "$version" == "$expected_version" ]] || fatal "Image manifest version does not match $expected_version"
  live_login_server=$(az acr show --name "$registry" --query loginServer -o tsv)
  [[ "$live_login_server" == "$expected_login_server" ]] || fatal "Azure registry login server does not match $expected_login_server"

  while IFS=$'\t' read -r name repository digest; do
    actual_digest=$(az acr manifest show-metadata --registry "$registry" \
      --name "${repository}:${version}" --query digest -o tsv)
    [[ "$actual_digest" == "$digest" ]] || fatal "ACR digest mismatch for $name (${repository}:${version})"
    attributes=$(az acr repository show --name "$registry" --image "${repository}:${version}" \
      --query '[changeableAttributes.writeEnabled,changeableAttributes.deleteEnabled]' -o json)
    jq -e '.[0] == false and .[1] == false' <<< "$attributes" >/dev/null || \
      fatal "ACR tag must disable writes and deletes: ${repository}:${version}"
  done < <(jq -r '.images | to_entries[] | [.key, .value.repository, .value.digest] | @tsv' "$manifest")

  info "Verified immutable ACR image manifest for $expected_login_server:$expected_version"
}

# Detect OSMO service URL from cluster (for CLI and external access)
detect_service_url() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local url=""
  local lb_ip
  lb_ip=$(kube_kubectl "$kubeconfig" "$context" get svc azureml-ingress-nginx-internal-lb -n azureml \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  if [[ -n "$lb_ip" ]]; then
    url="http://${lb_ip}"
  fi
  echo "$url"
}

# Print section header
section() {
  echo
  echo "============================"
  echo "$*"
  echo "============================"
}

# Print key-value pair for summaries
print_kv() {
  printf '%-18s %s\n' "$1:" "$2"
}

# Create NVCR image pull secret for NGC-authenticated registries
create_nvcr_pull_secret() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local ns="${3:?namespace required}" api_key_file="${4:?NGC API key file required}"
  local name="${5:-nvcr-pull-secret}" api_key auth docker_config
  require_protected_file "$api_key_file"
  api_key=$(<"$api_key_file")
  [[ -n "$api_key" ]] || fatal "NGC API key file is empty: $api_key_file"
  # shellcheck disable=SC2016  # NVCR requires the literal username $oauthtoken
  auth=$(printf '$oauthtoken:%s' "$api_key" | base64 | tr -d '\n')
  docker_config=$(mktemp)
  chmod 0600 "$docker_config"
  jq -n --arg auth "$auth" \
    '{auths: {"nvcr.io": {username: "$oauthtoken", auth: $auth}}}' > "$docker_config"
  info "Creating NVCR pull secret $name in namespace $ns..."
  if ! kube_kubectl "$kubeconfig" "$context" create secret generic "$name" \
    --namespace="$ns" \
    --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="$docker_config" \
    --dry-run=client -o yaml | kube_kubectl "$kubeconfig" "$context" apply -f -; then
    rm -f "$docker_config"
    unset api_key auth
    fatal "Failed to create NVCR pull secret $name in namespace $ns"
  fi
  rm -f "$docker_config"
  unset api_key auth
}

create_registry_pull_secret() {
  local kubeconfig="${1:?kubeconfig required}" context="${2:?context required}"
  local ns="${3:?namespace required}" docker_config_file="${4:?Docker config file required}"
  local name="${5:-registry-pull-secret}" registry_host="${6:?registry host required}"

  require_protected_file "$docker_config_file"
  jq -e --arg host "$registry_host" '
    .auths | type == "object" and
    .[$host] | type == "object" and
    (.auth | type == "string" and length > 0)
  ' "$docker_config_file" >/dev/null || \
    fatal "Registry Docker config has no auth entry for $registry_host"

  info "Creating registry pull secret $name in namespace $ns..."
  kube_kubectl "$kubeconfig" "$context" create secret generic "$name" \
    --namespace="$ns" \
    --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson="$docker_config_file" \
    --dry-run=client -o yaml | kube_kubectl "$kubeconfig" "$context" apply -f -
}

require_protected_directory() {
  local directory="${1:?protected directory required}" permissions owner current_user
  [[ -d "$directory" && ! -L "$directory" ]] || fatal "Protected directory must be a non-symlink directory: $directory"
  permissions=$(stat -c '%a' "$directory" 2>/dev/null || stat -f '%Lp' "$directory")
  owner=$(stat -c '%u' "$directory" 2>/dev/null || stat -f '%u' "$directory")
  current_user=$(id -u)
  [[ "$owner" == "0" || "$owner" == "$current_user" ]] || fatal "Protected directory has an unexpected owner: $directory"
  (( (8#$permissions & 8#077) == 0 )) || fatal "Protected directory must not be accessible by group or other users: $directory ($permissions)"
}

is_rfc1918_ipv4() {
  python3 - "$1" <<'PYTHON'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
networks = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
raise SystemExit(0 if isinstance(address, ipaddress.IPv4Address) and any(address in network for network in networks) else 1)
PYTHON
}

require_protected_file() {
  local file="${1:?protected file required}" permissions owner current_user
  [[ -f "$file" && ! -L "$file" ]] || fatal "Protected input must be a regular non-symlink file: $file"
  permissions=$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file")
  owner=$(stat -c '%u' "$file" 2>/dev/null || stat -f '%u' "$file")
  current_user=$(id -u)
  [[ "$owner" == "0" || "$owner" == "$current_user" ]] || fatal "Protected input has an unexpected owner: $file"
  (( (8#$permissions & 8#077) == 0 )) || fatal "Protected input must not be accessible by group or other users: $file ($permissions)"
}

# // ===================================================================
# OSMO Preflight Validation
# // ===================================================================

is_prerelease_tag() {
  local tag="${1:?image tag required}"
  [[ "$tag" =~ (rc|beta|alpha|pre|dev) ]] || [[ "$tag" =~ ^v20[0-9]{2}\. ]]
}

validate_version_pair() {
  local chart_version="${1:?chart version required}"
  local image_version="${2:?image version required}"
  local chart_version_set="${3:-false}"
  local image_version_set="${4:-false}"
  # shellcheck disable=SC2154
  local prerelease_chart="${5:-$OSMO_PRERELEASE_CHART_VERSION}"
  # shellcheck disable=SC2154
  local prerelease_image="${6:-$OSMO_PRERELEASE_IMAGE_VERSION}"

  [[ -n "$chart_version" ]] || fatal "Chart version cannot be empty"
  [[ -n "$image_version" ]] || fatal "Image version cannot be empty"

  if [[ "$chart_version_set" != "$image_version_set" ]]; then
    fatal "Use --chart-version and --image-version together to keep a tested chart/image pair"
  fi

  if is_prerelease_tag "$image_version"; then
    if [[ "$chart_version" != "$prerelease_chart" || "$image_version" != "$prerelease_image" ]]; then
      fatal "Unsupported prerelease pair: chart=${chart_version}, image=${image_version}. Set OSMO_PRERELEASE_CHART_VERSION/OSMO_PRERELEASE_IMAGE_VERSION to a tested pair, then retry."
    fi

    # shellcheck disable=SC2154
    if [[ "$OSMO_USE_PRERELEASE" != "true" && "$chart_version_set" != "true" ]]; then
      fatal "Prerelease image requires explicit opt-in. Set OSMO_USE_PRERELEASE=true or provide both --chart-version and --image-version."
    fi
  fi
}

# // ===================================================================
# OSMO Secrets
# // ===================================================================

# Apply SecretProviderClass for Azure Key Vault secrets sync
# Usage: apply_secret_provider_class <namespace> <keyvault> <client_id> <tenant_id> [include_redis_secret]
apply_secret_provider_class() {
  local namespace="${1:?namespace required}"
  local keyvault="${2:?keyvault name required}"
  local client_id="${3:?client_id required}"
  local tenant_id="${4:?tenant_id required}"
  local include_redis_secret="${5:-true}"

  local manifest_dir
  local _repo_root
  _repo_root="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd))"
  manifest_dir="${_repo_root}/infrastructure/setup/manifests"
  local manifest_name="aks-secret-provider-class.yaml"

  [[ "$include_redis_secret" == "true" ]] && manifest_name="aks-secret-provider-class-external-redis.yaml"

  export NAMESPACE="$namespace"
  export KEY_VAULT_NAME="$keyvault"
  export OSMO_CLIENT_ID="$client_id"
  export TENANT_ID="$tenant_id"

  info "Applying SecretProviderClass to namespace $namespace..."
  envsubst < "$manifest_dir/$manifest_name" | kubectl apply -f -
}
