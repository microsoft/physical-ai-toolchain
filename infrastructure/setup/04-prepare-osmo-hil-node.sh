#!/usr/bin/env bash
# Publish exact host-bound OSMO HiL inputs to pre-created Key Vault secrets.
# cspell:ignore fromdateiso noout outform pkey pubin readback
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/hil.sh
source "$REPO_ROOT/scripts/lib/hil.sh"
# shellcheck source=defaults.conf
source "$SCRIPT_DIR/defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") --environment NAME --host-name NAME [OPTIONS]

Verify an existing OSMO backend and pool, issue one service token, and publish
the exact host-bound HiL artifact catalog to pre-created Key Vault secrets.
This script does not create remote desired state or change Key Vault networking or RBAC.

OPTIONS:
    -h, --help                    Show this help message
    -e, --environment NAME        Existing environment bundle name (required)
    --host-name NAME              Ubuntu host identity (required)
    --tenant-id ID                Expected Microsoft Entra tenant (required)
    --subscription ID             Expected Azure subscription (required)
    --vault-name NAME             Existing Key Vault (required)
    --transport TRANSPORT         keyvault|scp (default: keyvault)
    --bundle-dir DIR              Generated non-secret environment bundle (required)
    --tf-dir DIR                  Terraform directory for generic bundle validation
    --service-url URL             Existing OSMO service URL (required)
    --backend-name NAME           Existing OSMO backend (required)
    --pool-name NAME              Existing OSMO pool (required)
    --osmo-config-dir DIR         Empty protected OSMO profile for fresh code login (required)
    --registry-config-file PATH   Protected pull-only Docker config (required)
    --token-expiry YYYY-MM-DD     Service-token expiry (required)
    --chart-version VERSION       Backend chart version (default: $OSMO_CHART_VERSION)
    --backend-chart-ref REF       Backend chart reference (default: osmo/$OSMO_BACKEND_CHART)
    --backend-chart-sha256 SHA    Expected backend chart SHA-256
    --image-version VERSION       OSMO image version (default: $OSMO_IMAGE_VERSION)
    --image-location PREFIX       OSMO image repository prefix (default: bundle registry/osmo)
    --vpn-input-dir DIR           Optional directory containing the four public VPN inputs
    --output-dir DIR              Optional protected directory for deliberate SCP transfer
    --publish-vpn-response PATH   Publish a CA-produced public response and update the catalog
    --csr-file PATH               Protected VPN request JSON for SCP response publication
    --catalog-file PATH           Existing protected catalog for response publication
    --config-preview              Print configuration and exit

EXAMPLES:
    $(basename "$0") --environment dev-001 --host-name hil-lab-01 \
      --tenant-id <tenant> --subscription <subscription> --vault-name <vault> \
      --bundle-dir generated/dev-001 --service-url https://osmo.example \
      --backend-name hil-lab-01 --pool-name hil-lab-01 \
      --osmo-config-dir /protected/osmo --registry-config-file /protected/config.json \
      --token-expiry 2026-08-01
EOF
}

environment=""
host_name=""
tenant_id=""
subscription_id=""
vault_name=""
transport="keyvault"
bundle_dir=""
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
service_url=""
backend_name=""
pool_name=""
osmo_config_dir=""
osmo_session_dir=""
registry_config_file=""
token_expiry=""
chart_version="$OSMO_CHART_VERSION"
backend_chart_ref="osmo/$OSMO_BACKEND_CHART"
backend_chart_sha256="$OSMO_BACKEND_CHART_SHA256"
image_version="$OSMO_IMAGE_VERSION"
image_location=""
vpn_input_dir=""
output_dir=""
vpn_response=""
csr_file=""
catalog_file=""
config_preview=false
catalog_activated=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                show_help; exit 0 ;;
    -e|--environment)         environment="$2"; shift 2 ;;
    --host-name)              host_name="$2"; shift 2 ;;
    --tenant-id)              tenant_id="$2"; shift 2 ;;
    --subscription)           subscription_id="$2"; shift 2 ;;
    --vault-name)             vault_name="$2"; shift 2 ;;
    --transport)              transport="$2"; shift 2 ;;
    --bundle-dir)             bundle_dir="$2"; shift 2 ;;
    --tf-dir)                 tf_dir="$2"; shift 2 ;;
    --service-url)            service_url="$2"; shift 2 ;;
    --backend-name)           backend_name="$2"; shift 2 ;;
    --pool-name)              pool_name="$2"; shift 2 ;;
    --osmo-config-dir)        osmo_config_dir="$2"; shift 2 ;;
    --registry-config-file)   registry_config_file="$2"; shift 2 ;;
    --token-expiry)           token_expiry="$2"; shift 2 ;;
    --chart-version)          chart_version="$2"; shift 2 ;;
    --backend-chart-ref)      backend_chart_ref="$2"; shift 2 ;;
    --backend-chart-sha256)   backend_chart_sha256="$2"; shift 2 ;;
    --image-version)          image_version="$2"; shift 2 ;;
    --image-location)         image_location="$2"; shift 2 ;;
    --vpn-input-dir)          vpn_input_dir="$2"; shift 2 ;;
    --output-dir)             output_dir="$2"; shift 2 ;;
    --publish-vpn-response)   vpn_response="$2"; shift 2 ;;
    --csr-file)               csr_file="$2"; shift 2 ;;
    --catalog-file)           catalog_file="$2"; shift 2 ;;
    --config-preview)         config_preview=true; shift ;;
    *)                        fatal "Unknown option: $1" ;;
  esac
done

hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"
[[ "$transport" == "keyvault" || "$transport" == "scp" ]] || fatal "--transport must be keyvault or scp"
[[ "$transport" != "scp" || -n "$output_dir" ]] || fatal "--output-dir is required for SCP transport"
catalog_secret="${environment}-${host_name}-hil-catalog"
csr_secret="${environment}-${host_name}-vpn-csr"
vpn_response_secret="${environment}-${host_name}-vpn-response"

if [[ -n "$vpn_response" ]]; then
  mode="vpn-response"
  [[ -n "$catalog_file" ]] || fatal "--catalog-file is required with --publish-vpn-response"
else
  mode="prepare"
  [[ -n "$bundle_dir" ]] || fatal "--bundle-dir is required"
  [[ -n "$service_url" ]] || fatal "--service-url is required"
  if [[ "$service_url" == http://* ]]; then
    service_host="${service_url#http://}"
    service_host="${service_host%%/*}"
    service_host="${service_host%%:*}"
    [[ "$service_host" =~ ^10\. || "$service_host" =~ ^192\.168\. || \
       "$service_host" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]] || \
      fatal "HTTP OSMO service URLs must use an RFC1918 address"
  elif [[ "$service_url" != https://* ]]; then
    fatal "OSMO service URL must use HTTPS or private RFC1918 HTTP"
  fi
  hil_require_name "Backend name" "$backend_name"
  hil_require_name "Pool name" "$pool_name"
  [[ -n "$osmo_config_dir" ]] || fatal "--osmo-config-dir is required"
  [[ -n "$registry_config_file" ]] || fatal "--registry-config-file is required"
  [[ "$token_expiry" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || fatal "--token-expiry must use YYYY-MM-DD"
  [[ "$backend_chart_sha256" =~ ^[0-9a-f]{64}$ ]] || fatal "--backend-chart-sha256 must contain 64 lowercase hexadecimal characters"
fi

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Mode" "$mode"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "Tenant" "$tenant_id"
  print_kv "Subscription" "$subscription_id"
  print_kv "Key Vault" "$vault_name"
  print_kv "Transport" "$transport"
  print_kv "Catalog Secret" "$catalog_secret"
  print_kv "CSR Secret" "$csr_secret"
  print_kv "VPN Response Secret" "$vpn_response_secret"
  print_kv "Service URL" "${service_url:-from catalog}"
  print_kv "Backend" "${backend_name:-from catalog}"
  print_kv "Pool" "${pool_name:-from catalog}"
  print_kv "Backend Chart" "$backend_chart_ref $chart_version"
  print_kv "Image Version" "$image_version"
  print_kv "VPN Inputs" "${vpn_input_dir:-not published}"
  print_kv "SCP Output" "${output_dir:-not written}"
  print_kv "CSR Source" "$([[ $transport == scp ]] && echo "${csr_file:-<required>}" || echo 'Key Vault')"
  print_kv "Network Changes" "none"
  print_kv "Role Changes" "none"
  exit 0
fi

operation="validate environment-owner inputs"
report_failure() {
  local status=$?
  error "Operation failed: $operation"
  error "Milestone incomplete: trusted environment inputs"
  if [[ -n "${work_dir:-}" && -f "$work_dir/published-versions.log" ]]; then
    if [[ "$catalog_activated" == "true" ]]; then
      error "The new catalog was published before this later failure."
    else
      error "The previous catalog remains active; these unreferenced versions may require cleanup:"
    fi
    while IFS='|' read -r published_secret published_version; do
      error "  $published_secret version $published_version"
    done < "$work_dir/published-versions.log"
  fi
  exit "$status"
}
trap report_failure ERR
require_tools jq openssl
if [[ "$transport" == "keyvault" ]]; then
  require_tools az
  account=$(az account show --output json)
  jq -e --arg tenant "$tenant_id" --arg subscription "$subscription_id" '
    ((.tenantId // "") | ascii_downcase) == ($tenant | ascii_downcase) and
    ((.id // "") | ascii_downcase) == ($subscription | ascii_downcase)
  ' <<< "$account" >/dev/null || fatal "Active Azure account does not match the expected tenant and subscription"
fi

work_dir=$(mktemp -d)
cleanup() {
  rm -rf "$work_dir"
  [[ -z "$osmo_session_dir" ]] || rm -rf "$osmo_session_dir"
}
trap cleanup EXIT
chmod 0700 "$work_dir"

secret_version() {
  local secret_name="${1:?secret name required}" file="${2:?file required}" content_type="${3:?content type required}"
  local secret_id version readback
  az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret_name" --query id -o tsv >/dev/null
  secret_id=$(az keyvault secret set --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret_name" --file "$file" --encoding utf-8 --content-type "$content_type" \
    --tags "physical-ai-environment=$environment" "physical-ai-host=$host_name" \
    --only-show-errors --query id -o tsv)
  version="${secret_id##*/}"
  printf '%s|%s\n' "$secret_name" "$version" >> "$work_dir/published-versions.log"
  readback=$(mktemp "$work_dir/.secret-readback.XXXXXX")
  az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret_name" --version "$version" --file "$readback" --encoding utf-8 \
    --overwrite --only-show-errors --output none
  [[ "$(calculate_sha256 "$readback")" == "$(calculate_sha256 "$file")" ]] || \
    fatal "Published Key Vault version failed digest read-back: $secret_name"
  rm -f "$readback"
  printf '%s\n' "$version"
}

verify_secret_version() {
  local secret_name="${1:?secret name required}" version="${2:?version required}" file="${3:?file required}"
  local readback
  readback=$(mktemp "$work_dir/.secret-readback.XXXXXX")
  az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret_name" --version "$version" --file "$readback" --encoding utf-8 \
    --overwrite --only-show-errors --output none
  [[ "$(calculate_sha256 "$readback")" == "$(calculate_sha256 "$file")" ]] || \
    fatal "Existing Key Vault version failed digest read-back: $secret_name"
  rm -f "$readback"
}

catalog_add_artifact() {
  local source_catalog="${1:?source catalog required}" target_catalog="${2:?target catalog required}"
  local key="${3:?key required}" file="${4:?file required}" secret="${5:?secret required}"
  local version="${6:?version required}" sha="${7:?digest required}"
  jq --arg key "$key" --arg file "$file" --arg secret "$secret" --arg version "$version" --arg sha "$sha" '
    .artifacts[$key] = {file: $file, secret_name: $secret, secret_version: $version, sha256: $sha}
  ' "$source_catalog" > "$target_catalog"
}

if [[ "$mode" == "vpn-response" ]]; then
  operation="validate VPN response and request binding"
  require_external_runtime_path "$catalog_file"
  require_external_runtime_path "$vpn_response"
  [[ -z "$csr_file" ]] || require_external_runtime_path "$csr_file"
  [[ -z "$output_dir" ]] || require_external_runtime_path "$output_dir"
  if [[ -n "$output_dir" ]]; then
    for output_file in vpn-response.json catalog.json; do
      [[ ! -L "$output_dir/$output_file" ]] || fatal "SCP output must not be a symlink: $output_file"
    done
  fi
  require_protected_file "$catalog_file"
  require_protected_file "$vpn_response"
  hil_reject_private_key_material "$vpn_response"
  hil_validate_catalog "$catalog_file" "$environment" "$host_name" "$tenant_id" "$subscription_id" "$vault_name"
  hil_validate_catalog_contract "$catalog_file" "$environment" "$host_name"
  [[ "$(jq -r '.csr_secret_name' "$catalog_file")" == "$csr_secret" ]] || fatal "Catalog CSR secret does not match"
  [[ "$(jq -r '.vpn_response_secret_name' "$catalog_file")" == "$vpn_response_secret" ]] || \
    fatal "Catalog VPN response secret does not match"
  jq -e --arg environment "$environment" --arg host "$host_name" '
    (keys | sort) == (["client_ca_certificate_pem", "client_certificate_pem", "csr_sha256",
      "environment", "host_name", "kind", "schema_version"] | sort) and
    .schema_version == 1 and .kind == "physical-ai-vpn-response" and
    .environment == $environment and .host_name == $host and
    (.csr_sha256 | test("^[0-9a-f]{64}$")) and
    (.client_certificate_pem | contains("BEGIN CERTIFICATE")) and
    (.client_ca_certificate_pem | contains("BEGIN CERTIFICATE"))
  ' "$vpn_response" >/dev/null || fatal "VPN response does not match the expected schema and host"
  if [[ "$transport" == "scp" ]]; then
    [[ -n "$csr_file" ]] || fatal "--csr-file is required for SCP response publication"
    require_protected_file "$csr_file"
    install -m 0600 "$csr_file" "$work_dir/vpn-request.json"
  else
    az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
      --name "$csr_secret" --file "$work_dir/vpn-request.json" --encoding utf-8 --overwrite \
      --only-show-errors --output none
  fi
  chmod 0600 "$work_dir/vpn-request.json"
  hil_reject_private_key_material "$work_dir/vpn-request.json"
  jq -e --arg environment "$environment" --arg host "$host_name" '
    (keys | sort) == (["client_root_sha256", "csr_pem", "csr_sha256", "environment", "gateway",
      "host_name", "kind", "schema_version", "server_root_sha256"] | sort) and
    .schema_version == 1 and .kind == "physical-ai-vpn-request" and
    .environment == $environment and .host_name == $host and
    (.csr_sha256 | test("^[0-9a-f]{64}$")) and (.csr_pem | contains("BEGIN CERTIFICATE REQUEST")) and
    (.client_root_sha256 | test("^[0-9a-f]{64}$")) and (.server_root_sha256 | test("^[0-9a-f]{64}$")) and
    (.gateway | test("^[A-Za-z0-9.-]+$"))
  ' "$work_dir/vpn-request.json" >/dev/null || fatal "Published VPN request does not match the environment and host"
  jq -r '.csr_pem' "$work_dir/vpn-request.json" > "$work_dir/client.csr"
  chmod 0600 "$work_dir/client.csr"
  [[ "$(calculate_sha256 "$work_dir/client.csr")" == "$(jq -r '.csr_sha256' "$work_dir/vpn-request.json")" ]] || \
    fatal "Published VPN request digest is invalid"
  [[ "$(calculate_sha256 "$work_dir/client.csr")" == "$(jq -r '.csr_sha256' "$vpn_response")" ]] || \
    fatal "VPN response does not bind to the published CSR"
  jq -r '.client_certificate_pem' "$vpn_response" > "$work_dir/client.pem"
  jq -r '.client_ca_certificate_pem' "$vpn_response" > "$work_dir/client-ca.pem"
  chmod 0600 "$work_dir/client.pem" "$work_dir/client-ca.pem"
  openssl verify -CAfile "$work_dir/client-ca.pem" "$work_dir/client.pem" >/dev/null
  csr_key=$(openssl req -in "$work_dir/client.csr" -pubkey -noout | openssl pkey -pubin -outform der | openssl sha256)
  cert_key=$(openssl x509 -in "$work_dir/client.pem" -pubkey -noout | openssl pkey -pubin -outform der | openssl sha256)
  [[ "$csr_key" == "$cert_key" ]] || fatal "VPN response certificate does not match the published CSR"
  client_root_sha=$(openssl x509 -in "$work_dir/client-ca.pem" -outform der | calculate_sha256 /dev/stdin)
  [[ "$client_root_sha" == "$(jq -r '.client_root_sha256' "$work_dir/vpn-request.json")" ]] || \
    fatal "VPN response chain does not match the published client root"

  jq -n --arg environment "$environment" --arg host "$host_name" \
    --arg csr_sha "$(jq -r '.csr_sha256' "$work_dir/vpn-request.json")" \
    --arg client_root_sha "$(jq -r '.client_root_sha256' "$work_dir/vpn-request.json")" \
    --arg server_root_sha "$(jq -r '.server_root_sha256' "$work_dir/vpn-request.json")" \
    --arg gateway "$(jq -r '.gateway' "$work_dir/vpn-request.json")" \
    --rawfile client_certificate "$work_dir/client.pem" --rawfile client_ca "$work_dir/client-ca.pem" '
    {schema_version: 1, kind: "physical-ai-vpn-response", environment: $environment,
     host_name: $host, csr_sha256: $csr_sha, client_root_sha256: $client_root_sha,
     server_root_sha256: $server_root_sha, gateway: $gateway,
     client_certificate_pem: $client_certificate, client_ca_certificate_pem: $client_ca}
  ' > "$work_dir/sanitized-vpn-response.json"
  chmod 0600 "$work_dir/sanitized-vpn-response.json"
  hil_reject_private_key_material "$work_dir/sanitized-vpn-response.json"

  operation="publish VPN response and updated catalog"
  if [[ "$transport" == "keyvault" ]]; then
    response_version=$(secret_version "$vpn_response_secret" "$work_dir/sanitized-vpn-response.json" application/json)
  else
    response_version=$(calculate_sha256 "$work_dir/sanitized-vpn-response.json")
  fi
  catalog_add_artifact "$catalog_file" "$work_dir/catalog.json" vpn_response vpn-response.json \
    "$vpn_response_secret" "$response_version" "$(calculate_sha256 "$work_dir/sanitized-vpn-response.json")"
  if [[ "$transport" == "keyvault" ]]; then
    secret_version "$catalog_secret" "$work_dir/catalog.json" application/json >/dev/null
    catalog_activated=true
  fi
  if [[ -n "$output_dir" ]]; then
    hil_prepare_directory "$output_dir"
    install -m 0600 "$work_dir/sanitized-vpn-response.json" "$output_dir/vpn-response.json"
    install -m 0600 "$work_dir/catalog.json" "$output_dir/catalog.json"
  fi
  trap - ERR
  section "Deployment Summary"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "VPN Response" "published"
  print_kv "Catalog" "published last"
  print_kv "SCP Output" "${output_dir:-not written}"
  info "Signed VPN response is ready for exact retrieval"
  exit 0
fi

operation="validate local publication files"
require_tools curl osmo
require_external_runtime_path "$osmo_config_dir"
require_external_runtime_path "$registry_config_file"
[[ -z "$vpn_input_dir" ]] || require_external_runtime_path "$vpn_input_dir"
[[ -z "$output_dir" ]] || require_external_runtime_path "$output_dir"
require_protected_directory "$osmo_config_dir"
osmo_session_dir=$(mktemp -d "$osmo_config_dir/session.XXXXXX")
chmod 0700 "$osmo_session_dir"
require_protected_file "$registry_config_file"
[[ -d "$bundle_dir" && ! -L "$bundle_dir" ]] || fatal "Bundle directory is unavailable: $bundle_dir"
deployment_file="$bundle_dir/deployment.json"
image_manifest="$bundle_dir/osmo-images.json"
[[ -f "$deployment_file" && ! -L "$deployment_file" ]] || fatal "deployment.json is required"
[[ -f "$image_manifest" && ! -L "$image_manifest" ]] || fatal "osmo-images.json is required"
jq -e --arg environment "$environment" --arg url "$service_url" \
  --arg chart "$chart_version" --arg image "$image_version" '
  .schema_version == 1 and .environment == $environment and .osmo_service_url == $url and
  .osmo_chart_version == $chart and .osmo_image_version == $image
' "$deployment_file" >/dev/null || fatal "Deployment metadata does not match the selected environment and OSMO URL"
registry_host=$(jq -r '.login_server // empty' "$image_manifest")
[[ -n "$registry_host" ]] || fatal "OSMO image manifest has no registry host"
image_location="${image_location:-$registry_host/osmo}"
jq -e --arg host "$registry_host" '(.auths | keys) == [$host] and (.auths[$host].auth | type == "string" and length > 0)' \
  "$registry_config_file" >/dev/null || fatal "Registry configuration does not contain the expected pull registry"
jq -e --arg host "$registry_host" --arg version "$image_version" '
  .schema_version == 1 and .login_server == $host and .image_version == $version and
  ((.images | keys | sort) == (["agent", "backend-listener", "backend-worker", "client",
    "delayed-job-monitor", "init-container", "logger", "router", "service", "web-ui", "worker"] | sort)) and
  all(.images[]; (.digest | test("^sha256:[0-9a-f]{64}$")))
' "$image_manifest" >/dev/null || fatal "OSMO image manifest does not match the selected registry and image version"
export XDG_CONFIG_HOME="$osmo_session_dir"
operation="authenticate to the explicit OSMO service URL"
osmo login "$service_url" --method code
operation="verify the explicit OSMO service version"
version_json=$(curl --fail --silent --show-error --connect-timeout 5 "${service_url%/}/api/version")
expected_major="${image_version%%.*}"
expected_minor="${image_version#*.}"
expected_minor="${expected_minor%%.*}"
jq -e --arg major "$expected_major" --arg minor "$expected_minor" '
  ((.major // "") | tostring) == $major and ((.minor // "") | tostring) == $minor
' <<< "$version_json" >/dev/null || fatal "OSMO service version does not match the selected image version"

operation="verify pre-created exchange secrets"
secret_names=(
  "$catalog_secret"
  "$csr_secret"
  "$vpn_response_secret"
  "${environment}-deployment"
  "${environment}-osmo-images"
  "${environment}-${host_name}-osmo-token"
  "${environment}-${host_name}-osmo-token-metadata"
  "${environment}-${host_name}-registry-config"
  "${environment}-${host_name}-osmo-artifacts"
)
if [[ -n "$vpn_input_dir" ]]; then
  secret_names+=(
    "${environment}-${host_name}-vpn-config"
    "${environment}-${host_name}-vpn-settings"
    "${environment}-${host_name}-vpn-server-root"
    "${environment}-${host_name}-vpn-client-root"
  )
fi
if [[ "$transport" == "keyvault" ]]; then
  for secret in "${secret_names[@]}"; do
    az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
      --name "$secret" --query id -o tsv >/dev/null
  done
fi

operation="verify existing OSMO backend"
backend_json=$(osmo config show BACKEND "$backend_name")
jq -e --arg backend "$backend_name" '
  (.name == $backend) or any(.backends[]?; .name == $backend)
' <<< "$backend_json" >/dev/null || fatal "OSMO backend response does not identify $backend_name"
operation="verify existing OSMO pool"
pool_json=$(osmo config show POOL "$pool_name")
jq -e --arg pool "$pool_name" --arg backend "$backend_name" '
  ((.name == $pool) and ((.backend // $backend) == $backend)) or
  any(.pools[]?; .name == $pool and ((.backend // $backend) == $backend))
' <<< "$pool_json" >/dev/null || fatal "OSMO pool response does not identify the expected backend relationship"

operation="issue least-privilege OSMO token"
token_name="${backend_name}-$(date -u +%Y%m%dT%H%M%SZ)"
osmo token set "$token_name" --expires-at "$token_expiry" \
  --description "Ubuntu HiL backend $backend_name" --service --roles osmo-backend \
  --format-type json > "$work_dir/token-response.json"
chmod 0600 "$work_dir/token-response.json"
token=$(jq -r '.token // empty' "$work_dir/token-response.json")
[[ -n "$token" ]] || fatal "OSMO token response did not contain a token"
printf '%s' "$token" > "$work_dir/osmo-token"
chmod 0600 "$work_dir/osmo-token"
unset token
token_sha=$(calculate_sha256 "$work_dir/osmo-token")
jq -n --arg environment "$environment" --arg host "$host_name" --arg backend "$backend_name" \
  --arg token_name "$token_name" --arg expiry "${token_expiry}T23:59:59Z" --arg sha "$token_sha" '
  {schema_version: 1, kind: "physical-ai-osmo-service-token", environment: $environment,
   host_name: $host, backend_name: $backend, token_name: $token_name, service: true,
   roles: ["osmo-backend"], expires_at: $expiry, token_sha256: $sha}
' > "$work_dir/osmo-token-metadata.json"
chmod 0600 "$work_dir/osmo-token-metadata.json"

service_major=$(jq -r '(.major // "") | tostring' <<< "$version_json")
service_minor=$(jq -r '(.minor // "") | tostring' <<< "$version_json")
operation="build immutable local deployment contract"
jq -n --arg environment "$environment" --arg host "$host_name" --arg service_url "$service_url" \
  --arg backend "$backend_name" --arg pool "$pool_name" --arg operator_namespace "$OSMO_HIL_OPERATOR_NAMESPACE" \
  --arg workflow_namespace "$OSMO_HIL_WORKFLOW_NAMESPACE" --arg kai_ref "$HELM_REPO_KAI/kai-scheduler" \
  --arg kai_version "$KAI_SCHEDULER_VERSION" --arg kai_sha "$KAI_SCHEDULER_CHART_SHA256" \
  --arg backend_ref "$backend_chart_ref" --arg backend_version "$chart_version" \
  --arg backend_sha "$backend_chart_sha256" --arg image_location "$image_location" \
  --arg image_version "$image_version" --arg registry_host "$registry_host" \
  --arg service_major "$service_major" --arg service_minor "$service_minor" '
  {schema_version: 1, kind: "physical-ai-hil-osmo", environment: $environment, host_name: $host,
   service_url: $service_url, service_version: {major: $service_major, minor: $service_minor},
   backend_name: $backend, pool_name: $pool,
   operator_namespace: $operator_namespace, workflow_namespace: $workflow_namespace,
   kai_chart: {ref: $kai_ref, version: $kai_version, sha256: $kai_sha},
   backend_chart: {ref: $backend_ref, version: $backend_version, sha256: $backend_sha},
   images: {location: $image_location, version: $image_version, registry_host: $registry_host,
            manifest_file: "osmo-images.json"}}
' > "$work_dir/osmo-artifacts.json"
chmod 0600 "$work_dir/osmo-artifacts.json"

operation="publish generic non-secret environment bundle"
if [[ "$transport" == "keyvault" ]]; then
  "$SCRIPT_DIR/upload-environment-bundle.sh" --environment "$environment" --tf-dir "$tf_dir" \
    --bundle-dir "$bundle_dir" --vault-name "$vault_name"
fi

operation="publish exact HiL artifacts"
if [[ -n "$output_dir" ]]; then
  hil_prepare_directory "$output_dir"
  for output_file in deployment.json osmo-images.json osmo-token osmo-token-metadata.json \
    registry-config.json osmo-artifacts.json vpn.json VpnSettings.xml VpnServerRoot.pem \
    ClientRoot.pem catalog.json; do
    [[ ! -L "$output_dir/$output_file" ]] || fatal "SCP output must not be a symlink: $output_file"
  done
fi
install -m 0600 "$registry_config_file" "$work_dir/registry-config.json"
jq -n --arg environment "$environment" --arg host "$host_name" --arg tenant "$tenant_id" \
  --arg subscription "$subscription_id" --arg vault "$vault_name" --arg csr "$csr_secret" \
  --arg response "$vpn_response_secret" '
  {schema_version: 1, kind: "physical-ai-hil-catalog", environment: $environment,
   host_name: $host, tenant_id: $tenant, subscription_id: $subscription, vault_name: $vault,
   csr_secret_name: $csr, vpn_response_secret_name: $response, artifacts: {}}
' > "$work_dir/catalog.json"
chmod 0600 "$work_dir/catalog.json"

add_published_file() {
  local key="${1:?key required}" file="${2:?file required}" secret="${3:?secret required}"
  local content_type="${4:?content type required}" version next_catalog
  if [[ "$transport" == "keyvault" ]]; then
    version=$(secret_version "$secret" "$file" "$content_type")
  else
    version=$(calculate_sha256 "$file")
  fi
  next_catalog="$work_dir/catalog-next.json"
  catalog_add_artifact "$work_dir/catalog.json" "$next_catalog" "$key" "$(basename "$file")" \
    "$secret" "$version" "$(calculate_sha256 "$file")"
  mv "$next_catalog" "$work_dir/catalog.json"
}

if [[ "$transport" == "keyvault" ]]; then
  deployment_version=$(az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "${environment}-deployment" --query id -o tsv)
  verify_secret_version "${environment}-deployment" "${deployment_version##*/}" "$deployment_file"
  deployment_version="${deployment_version##*/}"
else
  deployment_version=$(calculate_sha256 "$deployment_file")
fi
catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" deployment deployment.json \
  "${environment}-deployment" "$deployment_version" "$(calculate_sha256 "$deployment_file")"
mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
if [[ "$transport" == "keyvault" ]]; then
  image_version_id=$(az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "${environment}-osmo-images" --query id -o tsv)
  verify_secret_version "${environment}-osmo-images" "${image_version_id##*/}" "$image_manifest"
  image_version_id="${image_version_id##*/}"
else
  image_version_id=$(calculate_sha256 "$image_manifest")
fi
catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" image_manifest osmo-images.json \
  "${environment}-osmo-images" "$image_version_id" "$(calculate_sha256 "$image_manifest")"
mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
if [[ -n "$output_dir" ]]; then
  install -m 0600 "$deployment_file" "$output_dir/deployment.json"
  install -m 0600 "$image_manifest" "$output_dir/osmo-images.json"
fi
add_published_file osmo_token "$work_dir/osmo-token" "${environment}-${host_name}-osmo-token" text/plain
[[ -z "$output_dir" ]] || install -m 0600 "$work_dir/osmo-token" "$output_dir/osmo-token"
add_published_file osmo_token_metadata "$work_dir/osmo-token-metadata.json" \
  "${environment}-${host_name}-osmo-token-metadata" application/json
[[ -z "$output_dir" ]] || install -m 0600 "$work_dir/osmo-token-metadata.json" "$output_dir/osmo-token-metadata.json"
add_published_file registry_config "$work_dir/registry-config.json" \
  "${environment}-${host_name}-registry-config" application/json
[[ -z "$output_dir" ]] || install -m 0600 "$work_dir/registry-config.json" "$output_dir/registry-config.json"
add_published_file osmo_artifacts "$work_dir/osmo-artifacts.json" \
  "${environment}-${host_name}-osmo-artifacts" application/json
[[ -z "$output_dir" ]] || install -m 0600 "$work_dir/osmo-artifacts.json" "$output_dir/osmo-artifacts.json"

if [[ -n "$vpn_input_dir" ]]; then
  require_protected_directory "$vpn_input_dir"
  for file in vpn.json VpnSettings.xml VpnServerRoot.pem ClientRoot.pem; do
    require_protected_file "$vpn_input_dir/$file"
    hil_reject_private_key_material "$vpn_input_dir/$file"
  done
  jq -e --arg environment "$environment" --arg host "$host_name" '
    ((keys - ["schema_version", "kind", "environment", "host_name", "gateway", "p2s_cidr",
      "private_routes", "private_dns", "public_dns_canary"]) | length) == 0 and
    .schema_version == 1 and .kind == "physical-ai-vpn-inputs" and
    .environment == $environment and .host_name == $host and
    (.gateway | type == "string" and length > 0) and
    (.p2s_cidr | type == "string" and length > 0) and
    (.private_routes | type == "array" and length > 0) and
    ((.private_dns.server // "") == "" or
      ((.private_dns | keys | sort) == (["probes", "server", "zones"] | sort) and
       (.private_dns.zones | type == "array" and length > 0) and
       all(.private_dns.zones[]; test("^[A-Za-z0-9.-]+$")) and
       (.private_dns.probes | type == "array" and length > 0) and
       all(.private_dns.probes[];
         (keys | sort) == (["expected_cidr", "host"] | sort) and
         (.host | test("^[A-Za-z0-9.-]+$")) and (.expected_cidr | type == "string"))))
  ' "$vpn_input_dir/vpn.json" >/dev/null || fatal "VPN input metadata does not match the environment and host"
  grep -Fq "<VpnServer>$(jq -r '.gateway' "$vpn_input_dir/vpn.json")</VpnServer>" \
    "$vpn_input_dir/VpnSettings.xml" || fatal "VPN settings do not match the published gateway"
  openssl x509 -in "$vpn_input_dir/VpnServerRoot.pem" -noout -subject -issuer >/dev/null
  openssl x509 -in "$vpn_input_dir/ClientRoot.pem" -noout -subject -issuer >/dev/null
  add_published_file vpn_config "$vpn_input_dir/vpn.json" "${environment}-${host_name}-vpn-config" application/json
  add_published_file vpn_settings "$vpn_input_dir/VpnSettings.xml" \
    "${environment}-${host_name}-vpn-settings" application/xml
  add_published_file vpn_server_root "$vpn_input_dir/VpnServerRoot.pem" \
    "${environment}-${host_name}-vpn-server-root" application/x-pem-file
  add_published_file vpn_client_root "$vpn_input_dir/ClientRoot.pem" \
    "${environment}-${host_name}-vpn-client-root" application/x-pem-file
  if [[ -n "$output_dir" ]]; then
    install -m 0600 "$vpn_input_dir/vpn.json" "$output_dir/vpn.json"
    install -m 0600 "$vpn_input_dir/VpnSettings.xml" "$output_dir/VpnSettings.xml"
    install -m 0600 "$vpn_input_dir/VpnServerRoot.pem" "$output_dir/VpnServerRoot.pem"
    install -m 0600 "$vpn_input_dir/ClientRoot.pem" "$output_dir/ClientRoot.pem"
  fi
fi

operation="publish completion catalog last"
if [[ "$transport" == "keyvault" ]]; then
  secret_version "$catalog_secret" "$work_dir/catalog.json" application/json >/dev/null
  catalog_activated=true
fi
[[ -z "$output_dir" ]] || install -m 0600 "$work_dir/catalog.json" "$output_dir/catalog.json"

trap - ERR
section "Deployment Summary"
print_kv "Environment" "$environment"
print_kv "Host" "$host_name"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Key Vault" "$vault_name"
print_kv "Catalog" "published last"
print_kv "VPN Inputs" "$([[ -n $vpn_input_dir ]] && echo published || echo 'not requested')"
print_kv "SCP Output" "${output_dir:-not written}"
print_kv "RBAC" "unchanged; verify individual-secret assignments separately"
info "Trusted HiL environment inputs are published"
