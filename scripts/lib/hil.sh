#!/usr/bin/env bash
# Shared protected-transfer helpers for direct HiL setup scripts.

hil_require_name() {
  local label="${1:?label required}" value="${2:?value required}"
  [[ "$value" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || \
    fatal "$label must use lowercase letters, numbers, and internal hyphens"
}

hil_prepare_directory() {
  local directory="${1:?directory required}"
  require_external_runtime_path "$directory"
  require_no_symlink_path "$directory"
  if [[ -e "$directory" ]]; then
    require_protected_directory "$directory"
  else
    mkdir -p "$directory"
    chmod 0700 "$directory"
  fi
}

hil_reject_private_key_material() {
  local file="${1:?file required}"
  if grep -Eqi -- 'BEGIN (RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY' "$file"; then
    fatal "Public HiL artifact contains private-key material: $file"
  fi
}

hil_login_azure() {
  local tenant_id="${1:?tenant ID required}" subscription_id="${2:?subscription ID required}"
  local config_dir="${3:?Azure config directory required}" account

  hil_prepare_directory "$config_dir"
  export AZURE_CONFIG_DIR="$config_dir"
  az account clear >/dev/null 2>&1 || true
  az login --use-device-code --tenant "$tenant_id" --allow-no-subscriptions --output none
  az account set --subscription "$subscription_id"
  account=$(az account show --output json)
  jq -e --arg tenant "$tenant_id" --arg subscription "$subscription_id" '
    ((.tenantId // "") | ascii_downcase) == ($tenant | ascii_downcase) and
    ((.id // "") | ascii_downcase) == ($subscription | ascii_downcase)
  ' <<< "$account" >/dev/null || fatal "Azure account does not match the expected tenant and subscription"
}

hil_validate_catalog() {
  local catalog="${1:?catalog required}" environment="${2:?environment required}"
  local host_name="${3:?host name required}" tenant_id="${4:?tenant ID required}"
  local subscription_id="${5:?subscription ID required}" vault_name="${6:?vault name required}"

  require_protected_file "$catalog"
  jq -e --arg environment "$environment" --arg host "$host_name" --arg tenant "$tenant_id" \
    --arg subscription "$subscription_id" --arg vault "$vault_name" \
    --arg csr "${environment}-${host_name}-vpn-csr" \
    --arg response "${environment}-${host_name}-vpn-response" '
    (keys | sort) == (["artifacts", "csr_secret_name", "environment", "host_name", "kind",
      "schema_version", "subscription_id", "tenant_id", "vault_name", "vpn_response_secret_name"] | sort) and
    .schema_version == 1 and
    .kind == "physical-ai-hil-catalog" and
    .environment == $environment and
    .host_name == $host and
    ((.tenant_id // "") | ascii_downcase) == ($tenant | ascii_downcase) and
    ((.subscription_id // "") | ascii_downcase) == ($subscription | ascii_downcase) and
    .vault_name == $vault and
    (.artifacts | type == "object") and
    .csr_secret_name == $csr and
    .vpn_response_secret_name == $response and
    all(.artifacts[]; (keys | sort) == (["file", "secret_name", "secret_version", "sha256"] | sort))
  ' "$catalog" >/dev/null || fatal "HiL catalog does not match the expected environment and host"
}

hil_artifact_contract() {
  local key="${1:?artifact key required}" environment="${2:?environment required}"
  local host_name="${3:?host name required}"
  case "$key" in
    deployment)           printf 'deployment.json|%s-deployment\n' "$environment" ;;
    image_manifest)       printf 'osmo-images.json|%s-osmo-images\n' "$environment" ;;
    osmo_token)           printf 'osmo-token|%s-%s-osmo-token\n' "$environment" "$host_name" ;;
    osmo_token_metadata)  printf 'osmo-token-metadata.json|%s-%s-osmo-token-metadata\n' "$environment" "$host_name" ;;
    registry_config)      printf 'registry-config.json|%s-%s-registry-config\n' "$environment" "$host_name" ;;
    osmo_artifacts)       printf 'osmo-artifacts.json|%s-%s-osmo-artifacts\n' "$environment" "$host_name" ;;
    vpn_config)           printf 'vpn.json|%s-%s-vpn-config\n' "$environment" "$host_name" ;;
    vpn_settings)         printf 'VpnSettings.xml|%s-%s-vpn-settings\n' "$environment" "$host_name" ;;
    vpn_server_root)      printf 'VpnServerRoot.pem|%s-%s-vpn-server-root\n' "$environment" "$host_name" ;;
    vpn_client_root)      printf 'ClientRoot.pem|%s-%s-vpn-client-root\n' "$environment" "$host_name" ;;
    vpn_response)         printf 'vpn-response.json|%s-%s-vpn-response\n' "$environment" "$host_name" ;;
    *) fatal "Unsupported HiL artifact key: $key" ;;
  esac
}

hil_validate_catalog_contract() {
  local catalog="${1:?catalog required}" environment="${2:?environment required}"
  local host_name="${3:?host name required}" allowed_keys duplicate_files key contract
  local expected_file expected_secret file secret

  allowed_keys=$(jq -n '["deployment", "image_manifest", "osmo_token", "osmo_token_metadata",
    "registry_config", "osmo_artifacts", "vpn_config", "vpn_settings", "vpn_server_root",
    "vpn_client_root", "vpn_response"] | sort')
  jq -e --argjson allowed "$allowed_keys" '
    (.artifacts | keys | length) >= 6 and
    ((.artifacts | keys) - $allowed | length) == 0 and
    (["deployment", "image_manifest", "osmo_token", "osmo_token_metadata", "registry_config", "osmo_artifacts"] - (.artifacts | keys) | length) == 0 and
    (([.artifacts | keys[] | select(. == "vpn_config" or . == "vpn_settings" or . == "vpn_server_root" or . == "vpn_client_root")] | length) as $vpn_count |
      ($vpn_count == 0 or $vpn_count == 4) and
      ((.artifacts | has("vpn_response") | not) or $vpn_count == 4)) and
    all(.artifacts[]; (.secret_version | test("^[A-Za-z0-9]+$")) and (.sha256 | test("^[0-9a-f]{64}$")))
  ' "$catalog" >/dev/null || fatal "HiL catalog does not contain one supported exact artifact set"
  duplicate_files=$(jq '[.artifacts[].file] | length != (unique | length)' "$catalog")
  [[ "$duplicate_files" == "false" ]] || fatal "HiL catalog contains duplicate artifact filenames"
  while IFS= read -r key; do
    contract=$(hil_artifact_contract "$key" "$environment" "$host_name")
    expected_file="${contract%%|*}"
    expected_secret="${contract#*|}"
    file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
    secret=$(jq -r --arg key "$key" '.artifacts[$key].secret_name // empty' "$catalog")
    [[ "$file" == "$expected_file" && "$file" != "catalog.json" ]] || fatal "Catalog filename does not match $key"
    [[ "$secret" == "$expected_secret" ]] || fatal "Catalog secret name does not match $key"
  done < <(jq -r '.artifacts | keys[]' "$catalog")
}

hil_require_local_artifact() {
  local catalog="${1:?catalog required}" key="${2:?artifact key required}"
  local environment="${3:?environment required}" host_name="${4:?host name required}"
  local directory="${5:?artifact directory required}" contract expected_file expected_secret
  local file secret expected_sha path

  require_protected_file "$catalog"
  contract=$(hil_artifact_contract "$key" "$environment" "$host_name")
  expected_file="${contract%%|*}"
  expected_secret="${contract#*|}"
  file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
  secret=$(jq -r --arg key "$key" '.artifacts[$key].secret_name // empty' "$catalog")
  expected_sha=$(jq -r --arg key "$key" '.artifacts[$key].sha256 // empty' "$catalog")
  [[ "$file" == "$expected_file" && "$secret" == "$expected_secret" ]] || \
    fatal "Local catalog mapping does not match $key"
  [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fatal "Local catalog has no valid digest for $key"
  path="$directory/$file"
  require_protected_file "$path"
  [[ "$(calculate_sha256 "$path")" == "$expected_sha" ]] || fatal "Local artifact digest mismatch: $file"
  printf '%s\n' "$path"
}

hil_require_local_k3s_identity() {
  local identity_file="${1:?identity file required}" kubeconfig="${2:?kubeconfig required}"
  local context="${3:?context required}" expected_node="${4:-}"
  local live_server live_namespace_uid live_node identity_json identity_owner identity_mode

  require_tools sudo
  sudo test -f "$identity_file" || fatal "Local K3s identity is not a regular protected file"
  sudo test ! -L "$identity_file" || fatal "Local K3s identity must not be a symlink"
  identity_owner=$(sudo stat -c '%u' "$identity_file")
  identity_mode=$(sudo stat -c '%a' "$identity_file")
  [[ "$identity_owner" == "0" && "$identity_mode" == "600" ]] || \
    fatal "Local K3s identity must be root-owned with mode 0600"
  identity_json=$(sudo cat "$identity_file")
  require_protected_file "$kubeconfig"
  verify_kube_target "$kubeconfig" "$context" k3s
  live_server=$(kube_api_server "$kubeconfig" "$context")
  live_namespace_uid=$(kube_system_namespace_uid "$kubeconfig" "$context")
  live_node=$(kube_kubectl "$kubeconfig" "$context" get nodes -o jsonpath='{.items[0].metadata.name}')
  jq -e --arg kubeconfig "$kubeconfig" --arg context "$context" --arg server "$live_server" \
    --arg namespace_uid "$live_namespace_uid" --arg node "$live_node" --arg expected_node "$expected_node" '
    .schema_version == 1 and .kind == "physical-ai-local-k3s" and
    .kubeconfig == $kubeconfig and .context == $context and .api_server == $server and
    .kube_system_namespace_uid == $namespace_uid and .node_name == $node and
    ($expected_node == "" or .node_name == $expected_node)
  ' <<< "$identity_json" >/dev/null || fatal "K3s target does not match the root-owned local identity"
}

hil_fetch_artifacts() (
  local transport="${1:?transport required}" catalog_secret="${2:?catalog secret required}"
  local environment="${3:?environment required}" host_name="${4:?host name required}"
  local tenant_id="${5:?tenant ID required}" subscription_id="${6:?subscription ID required}"
  local vault_name="${7:?vault name required}" output_dir="${8:?output directory required}"
  local scp_source="${9:-}"
  shift 9
  local keys=("$@") parent staging catalog source_file key file secret version expected_sha actual_sha
  local contract expected_file expected_secret
  local backup="" staging=""

  # shellcheck disable=SC2329  # invoked by the EXIT trap
  cleanup_hil_fetch() {
    [[ -z "$staging" ]] || rm -rf "$staging"
  }
  trap cleanup_hil_fetch EXIT

  [[ "$transport" == "keyvault" || "$transport" == "scp" ]] || fatal "Transport must be keyvault or scp"
  (( ${#keys[@]} > 0 )) || fatal "At least one catalog artifact is required"
  parent=$(dirname "$output_dir")
  hil_prepare_directory "$parent"
  [[ ! -L "$output_dir" ]] || fatal "HiL input directory must not be a symlink: $output_dir"
  if [[ -e "$output_dir" ]]; then
    require_protected_directory "$output_dir"
  fi

  staging=$(mktemp -d "${output_dir}.tmp.XXXXXX")
  chmod 0700 "$staging"
  catalog="$staging/catalog.json"
  if [[ "$transport" == "keyvault" ]]; then
    az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
      --name "$catalog_secret" --file "$catalog" --encoding utf-8 --overwrite \
      --only-show-errors --output none
  else
    [[ -n "$scp_source" ]] || fatal "--scp-source-dir is required for SCP transport"
    require_external_runtime_path "$scp_source"
    require_protected_directory "$scp_source"
    source_file="$scp_source/catalog.json"
    require_protected_file "$source_file"
    install -m 0600 "$source_file" "$catalog"
  fi
  chmod 0600 "$catalog"
  hil_validate_catalog "$catalog" "$environment" "$host_name" "$tenant_id" "$subscription_id" "$vault_name"
  hil_validate_catalog_contract "$catalog" "$environment" "$host_name"

  for key in "${keys[@]}"; do
    [[ "$key" =~ ^[a-z][a-z0-9_]*$ ]] || fatal "Invalid catalog artifact key: $key"
    contract=$(hil_artifact_contract "$key" "$environment" "$host_name")
    expected_file="${contract%%|*}"
    expected_secret="${contract#*|}"
    jq -e --arg key "$key" '.artifacts | has($key)' "$catalog" >/dev/null || fatal "HiL catalog is missing $key"
    file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
    secret=$(jq -r --arg key "$key" '.artifacts[$key].secret_name // empty' "$catalog")
    version=$(jq -r --arg key "$key" '.artifacts[$key].secret_version // empty' "$catalog")
    expected_sha=$(jq -r --arg key "$key" '.artifacts[$key].sha256 // empty' "$catalog")
    [[ "$file" == "$expected_file" && "$file" != "catalog.json" ]] || fatal "Catalog filename does not match $key"
    [[ "$secret" == "$expected_secret" ]] || fatal "Catalog secret name does not match $key"
    [[ "$version" =~ ^[A-Za-z0-9]+$ ]] || fatal "Catalog has no immutable secret version for $key"
    [[ "$expected_sha" =~ ^[0-9a-f]{64}$ ]] || fatal "Catalog has no valid SHA-256 for $key"
    if [[ "$transport" == "keyvault" ]]; then
      az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
        --name "$secret" --version "$version" --file "$staging/$file" --encoding utf-8 \
        --overwrite --only-show-errors --output none
    else
      source_file="$scp_source/$file"
      require_protected_file "$source_file"
      install -m 0600 "$source_file" "$staging/$file"
    fi
    chmod 0600 "$staging/$file"
    actual_sha=$(calculate_sha256 "$staging/$file")
    [[ "$actual_sha" == "$expected_sha" ]] || fatal "Artifact digest mismatch: $file"
  done

  if [[ -d "$output_dir" ]]; then
    backup=$(mktemp -d "${output_dir}.backup.XXXXXX")
    rmdir "$backup"
    mv "$output_dir" "$backup"
  fi
  if mv "$staging" "$output_dir"; then
    staging=""
    [[ -z "$backup" ]] || rm -rf "$backup"
  else
    [[ -z "$backup" ]] || mv "$backup" "$output_dir"
    fatal "Unable to install validated HiL inputs"
  fi
)

hil_wait_for_workflow() {
  local workflow_id="${1:?workflow ID required}" timeout_seconds="${2:-600}"
  local started_at status_json status

  started_at=$(date +%s)
  while (( $(date +%s) - started_at < timeout_seconds )); do
    status_json=$(osmo workflow query "$workflow_id" --format-type json)
    status=$(jq -r '.status // .state // empty' <<< "$status_json")
    case "$status" in
      COMPLETED|completed|Completed|SUCCEEDED|succeeded|Succeeded)
        return 0
        ;;
      FAILED|failed|Failed|ERROR|error|Error)
        error "OSMO workflow failed: $workflow_id"
        return 1
        ;;
      CANCELLED|cancelled|Canceled|CANCELED|canceled)
        error "OSMO workflow was cancelled: $workflow_id"
        return 1
        ;;
    esac
    sleep 5
  done
  error "OSMO workflow did not complete within ${timeout_seconds}s: $workflow_id"
  return 1
}
