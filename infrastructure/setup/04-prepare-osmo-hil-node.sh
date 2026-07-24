#!/usr/bin/env bash
# Publish exact host-bound OSMO HiL inputs to pre-created Key Vault secrets.
# cspell:ignore connectedclusters fromdateiso noout outform pkey pubin readback
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

Verify an existing OSMO backend and pool, reuse or issue one service token, and publish
the exact host-bound HiL artifact catalog to pre-created Key Vault secrets.
This script does not create remote desired state or change Key Vault networking or RBAC.

OPTIONS:
    -h, --help                    Show this help message
    -e, --environment NAME        Existing environment bundle name (required)
    --host-name NAME              Ubuntu host identity (required)
    --tenant-id ID                Expected Microsoft Entra tenant (required)
    --subscription ID             Expected Azure subscription (required)
    --vault-name NAME             Existing Key Vault (required)
    --bundle-dir DIR              Generated non-secret environment bundle (required)
    --tf-dir DIR                  Terraform directory for generic bundle validation
    --service-url URL             Existing OSMO service URL (required)
    --backend-name NAME           Existing OSMO backend (required)
    --pool-name NAME              Existing OSMO pool (required)
    --osmo-config-dir DIR         Empty protected OSMO profile for fresh code login (required)
    --registry-config-file PATH   Protected pull-only Docker config (required)
    --token-expiry YYYY-MM-DD     Service-token expiry (required)
    --arc-cluster-resource-id ID  Arc-enabled Kubernetes resource ID (required for prepare)
    --renew-token                 Issue a new token after validating the current catalog
    --chart-version VERSION       Backend chart version (default: $OSMO_CHART_VERSION)
    --backend-chart-ref REF       Backend chart reference (default: osmo/$OSMO_BACKEND_CHART)
    --backend-chart-sha256 SHA    Expected backend chart SHA-256
    --image-version VERSION       OSMO image version (default: $OSMO_IMAGE_VERSION)
    --image-location PREFIX       OSMO image repository prefix (default: bundle registry/osmo)
    --vpn-input-dir DIR           Optional directory containing the four public VPN inputs
    --publish-vpn-response PATH   Publish a CA-produced public response and update the catalog
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
bundle_dir=""
tf_dir="$SCRIPT_DIR/$DEFAULT_TF_DIR"
service_url=""
backend_name=""
pool_name=""
osmo_config_dir=""
osmo_session_dir=""
registry_config_file=""
token_expiry=""
arc_cluster_resource_id=""
renew_token=false
chart_version="$OSMO_CHART_VERSION"
backend_chart_ref="osmo/$OSMO_BACKEND_CHART"
backend_chart_sha256="$OSMO_BACKEND_CHART_SHA256"
image_version="$OSMO_IMAGE_VERSION"
image_location=""
vpn_input_dir=""
vpn_response=""
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
    --bundle-dir)             bundle_dir="$2"; shift 2 ;;
    --tf-dir)                 tf_dir="$2"; shift 2 ;;
    --service-url)            service_url="$2"; shift 2 ;;
    --backend-name)           backend_name="$2"; shift 2 ;;
    --pool-name)              pool_name="$2"; shift 2 ;;
    --osmo-config-dir)        osmo_config_dir="$2"; shift 2 ;;
    --registry-config-file)   registry_config_file="$2"; shift 2 ;;
    --token-expiry)           token_expiry="$2"; shift 2 ;;
    --arc-cluster-resource-id) arc_cluster_resource_id="$2"; shift 2 ;;
    --renew-token)            renew_token=true; shift ;;
    --chart-version)          chart_version="$2"; shift 2 ;;
    --backend-chart-ref)      backend_chart_ref="$2"; shift 2 ;;
    --backend-chart-sha256)   backend_chart_sha256="$2"; shift 2 ;;
    --image-version)          image_version="$2"; shift 2 ;;
    --image-location)         image_location="$2"; shift 2 ;;
    --vpn-input-dir)          vpn_input_dir="$2"; shift 2 ;;
    --publish-vpn-response)   vpn_response="$2"; shift 2 ;;
    --config-preview)         config_preview=true; shift ;;
    *)                        fatal "Unknown option: $1" ;;
  esac
done

hil_require_name "Environment" "$environment"
hil_require_name "Host name" "$host_name"
[[ -n "$tenant_id" ]] || fatal "--tenant-id is required"
[[ -n "$subscription_id" ]] || fatal "--subscription is required"
[[ -n "$vault_name" ]] || fatal "--vault-name is required"
catalog_secret="${environment}-${host_name}-hil-catalog"
csr_secret="${environment}-${host_name}-vpn-csr"
vpn_response_secret="${environment}-${host_name}-vpn-response"

if [[ -n "$vpn_response" ]]; then
  mode="vpn-response"
  [[ "$renew_token" == "false" ]] || fatal "--renew-token is only valid when publishing HiL artifacts"
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
  [[ -n "$arc_cluster_resource_id" ]] || fatal "--arc-cluster-resource-id is required"
  arc_cluster_resource_id_lower=$(printf '%s' "$arc_cluster_resource_id" | tr '[:upper:]' '[:lower:]')
  [[ "$arc_cluster_resource_id_lower" =~ ^/subscriptions/([0-9a-f-]{36})/resourcegroups/([^/]+)/providers/microsoft.kubernetes/connectedclusters/([^/]+)$ ]] || \
    fatal "--arc-cluster-resource-id must identify a Microsoft.Kubernetes/connectedClusters resource"
  arc_cluster_subscription_id="${BASH_REMATCH[1]}"
  [[ "$arc_cluster_subscription_id" == "$(printf '%s' "$subscription_id" | tr '[:upper:]' '[:lower:]')" ]] || \
    fatal "--arc-cluster-resource-id subscription does not match --subscription"
  token_expiry_utc="${token_expiry}T23:59:59Z"
  jq -n -e --arg expiry "$token_expiry_utc" '$expiry | fromdateiso8601 > now' >/dev/null || \
    fatal "--token-expiry must be in the future"
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
  print_kv "Catalog Secret" "$catalog_secret"
  print_kv "CSR Secret" "$csr_secret"
  print_kv "VPN Response Secret" "$vpn_response_secret"
  print_kv "Service URL" "${service_url:-from catalog}"
  print_kv "Backend" "${backend_name:-from catalog}"
  print_kv "Pool" "${pool_name:-from catalog}"
  print_kv "Arc Cluster Resource ID" "${arc_cluster_resource_id:-not applicable}"
  print_kv "Renew Token" "$renew_token"
  print_kv "Token Expiry" "${token_expiry:-not applicable}"
  print_kv "Backend Chart" "$backend_chart_ref $chart_version"
  print_kv "Image Version" "$image_version"
  print_kv "VPN Inputs" "${vpn_input_dir:-not published}"
  print_kv "Token Policy" "reuse valid catalog token unless renewal is requested"
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
require_tools az jq openssl
account=$(az account show --output json)
jq -e --arg tenant "$tenant_id" --arg subscription "$subscription_id" '
  ((.tenantId // "") | ascii_downcase) == ($tenant | ascii_downcase) and
  ((.id // "") | ascii_downcase) == ($subscription | ascii_downcase)
' <<< "$account" >/dev/null || fatal "Active Azure account does not match the expected tenant and subscription"

if [[ "$mode" == "prepare" ]]; then
  operation="verify Arc workload identity target"
  arc_cluster=$(az connectedk8s show --ids "$arc_cluster_resource_id" --output json)
  jq -e --arg id "$arc_cluster_resource_id" '
    ((.id // "") | ascii_downcase) == ($id | ascii_downcase) and
    .securityProfile.workloadIdentity.enabled == true and
    (.oidcIssuerProfile.issuerUrl | type == "string" and test("^https://[^[:space:]]+$"))
  ' <<< "$arc_cluster" >/dev/null || \
    fatal "Arc cluster must have workload identity enabled and an OIDC issuer"
  arc_oidc_issuer=$(jq -r '.oidcIssuerProfile.issuerUrl' <<< "$arc_cluster")
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
  local allow_absent="${4:-false}"
  local secret_id version readback
  if ! az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret_name" --query id -o tsv >/dev/null 2>"$work_dir/secret-show-error.log"; then
    if [[ "$allow_absent" != "true" ]] || ! hil_key_vault_secret_not_found "$work_dir/secret-show-error.log"; then
      fatal "Required Key Vault secret is unavailable: $secret_name"
    fi
  fi
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
  require_external_runtime_path "$vpn_response"
  require_protected_file "$vpn_response"
  hil_reject_private_key_material "$vpn_response"
  az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$catalog_secret" --file "$work_dir/catalog.json" --encoding utf-8 --overwrite \
    --only-show-errors --output none
  chmod 0600 "$work_dir/catalog.json"
  hil_validate_catalog "$work_dir/catalog.json" "$environment" "$host_name" "$tenant_id" "$subscription_id" "$vault_name"
  hil_validate_catalog_contract "$work_dir/catalog.json" "$environment" "$host_name"
  [[ "$(jq -r '.csr_secret_name' "$work_dir/catalog.json")" == "$csr_secret" ]] || fatal "Catalog CSR secret does not match"
  [[ "$(jq -r '.vpn_response_secret_name' "$work_dir/catalog.json")" == "$vpn_response_secret" ]] || \
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
  az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$csr_secret" --file "$work_dir/vpn-request.json" --encoding utf-8 --overwrite \
    --only-show-errors --output none
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
  response_version=$(secret_version "$vpn_response_secret" "$work_dir/sanitized-vpn-response.json" application/json)
  catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" vpn_response vpn-response.json \
    "$vpn_response_secret" "$response_version" "$(calculate_sha256 "$work_dir/sanitized-vpn-response.json")"
  mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
  secret_version "$catalog_secret" "$work_dir/catalog.json" application/json true >/dev/null
  catalog_activated=true
  trap - ERR
  section "Deployment Summary"
  print_kv "Environment" "$environment"
  print_kv "Host" "$host_name"
  print_kv "VPN Response" "published"
  print_kv "Catalog" "published last"
  info "Signed VPN response is ready for exact retrieval"
  exit 0
fi

operation="validate local publication files"
require_tools curl osmo
require_external_runtime_path "$osmo_config_dir"
require_external_runtime_path "$registry_config_file"
[[ -z "$vpn_input_dir" ]] || require_external_runtime_path "$vpn_input_dir"
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
  .osmo_chart_version == $chart and .osmo_image_version == $image and
  (.osmo_workflow_data_uri | type == "string" and
    test("^azure://[a-z0-9]{3,24}/[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?/workflows/data$")) and
  (.osmo_workload_identity | type == "object" and
    (.id | type == "string" and length > 0) and
    (.principal_id | type == "string" and length > 0) and
    (.client_id | type == "string" and length > 0) and
    (.tenant_id | type == "string" and length > 0))
' "$deployment_file" >/dev/null || fatal "Deployment metadata does not match the selected environment and OSMO URL"
workflow_data_uri=$(jq -r '.osmo_workflow_data_uri' "$deployment_file")
osmo_identity_id=$(jq -r '.osmo_workload_identity.id' "$deployment_file")
osmo_identity_principal_id=$(jq -r '.osmo_workload_identity.principal_id' "$deployment_file")
osmo_identity_client_id=$(jq -r '.osmo_workload_identity.client_id' "$deployment_file")
osmo_identity_tenant_id=$(jq -r '.osmo_workload_identity.tenant_id' "$deployment_file")
[[ "$(printf '%s' "$osmo_identity_tenant_id" | tr '[:upper:]' '[:lower:]')" == \
  "$(printf '%s' "$tenant_id" | tr '[:upper:]' '[:lower:]')" ]] || \
  fatal "Deployment OSMO workload identity tenant does not match --tenant-id"
operation="verify OSMO workload identity"
osmo_identity=$(az identity show --ids "$osmo_identity_id" --output json)
jq -e --arg id "$osmo_identity_id" --arg principal_id "$osmo_identity_principal_id" \
  --arg client_id "$osmo_identity_client_id" --arg tenant_id "$osmo_identity_tenant_id" '
  ((.id // "") | ascii_downcase) == ($id | ascii_downcase) and
  .principalId == $principal_id and .clientId == $client_id and .tenantId == $tenant_id and
  (.name | type == "string" and length > 0) and (.resourceGroup | type == "string" and length > 0)
' <<< "$osmo_identity" >/dev/null || fatal "Live OSMO workload identity does not match deployment metadata"
osmo_identity_name=$(jq -r '.name' <<< "$osmo_identity")
osmo_identity_resource_group=$(jq -r '.resourceGroup' <<< "$osmo_identity")
workflow_service_account="osmo-workflow"
workflow_service_account_subject="system:serviceaccount:${OSMO_HIL_WORKFLOW_NAMESPACE}:${workflow_service_account}"
arc_cluster_hash=$(printf '%s' "$arc_cluster_resource_id" | calculate_sha256 /dev/stdin | cut -c1-16)
federated_credential_name="osmo-hil-${host_name}-${arc_cluster_hash}"
[[ "$federated_credential_name" =~ ^[A-Za-z0-9_-]{3,120}$ ]] || \
  fatal "Derived federated credential name is outside Azure limits"
operation="reconcile Arc workload identity federation"
fic_error="$work_dir/federated-credential-show-error.log"
if federated_credential=$(az identity federated-credential show --identity-name "$osmo_identity_name" \
  --resource-group "$osmo_identity_resource_group" --subscription "$subscription_id" \
  --name "$federated_credential_name" --output json 2>"$fic_error"); then
  jq -e --arg issuer "$arc_oidc_issuer" --arg subject "$workflow_service_account_subject" '
    .issuer == $issuer and .subject == $subject and .audiences == ["api://AzureADTokenExchange"]
  ' <<< "$federated_credential" >/dev/null || \
    fatal "Existing federated credential does not match the Arc service-account contract"
else
  grep -Eqi 'ResourceNotFound|404|NotFound' "$fic_error" || \
    fatal "Unable to read the expected federated credential"
  federated_credential=$(az identity federated-credential create --identity-name "$osmo_identity_name" \
    --resource-group "$osmo_identity_resource_group" --subscription "$subscription_id" \
    --name "$federated_credential_name" --issuer "$arc_oidc_issuer" \
    --subject "$workflow_service_account_subject" \
    --audiences "api://AzureADTokenExchange" --output json)
  jq -e --arg issuer "$arc_oidc_issuer" --arg subject "$workflow_service_account_subject" '
    .issuer == $issuer and .subject == $subject and .audiences == ["api://AzureADTokenExchange"]
  ' <<< "$federated_credential" >/dev/null || \
    fatal "Created federated credential does not match the Arc service-account contract"
fi
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
for secret in "${secret_names[@]}"; do
  az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret" --query id -o tsv >/dev/null
done

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
  --arg service_major "$service_major" --arg service_minor "$service_minor" \
  --arg workflow_data_uri "$workflow_data_uri" --arg identity_id "$osmo_identity_id" \
  --arg identity_client_id "$osmo_identity_client_id" --arg identity_tenant_id "$osmo_identity_tenant_id" \
  --arg arc_resource_id "$arc_cluster_resource_id" --arg arc_oidc_issuer "$arc_oidc_issuer" \
  --arg fic_name "$federated_credential_name" --arg service_account "$workflow_service_account" \
  --arg service_account_subject "$workflow_service_account_subject" '
  {schema_version: 1, kind: "physical-ai-hil-osmo", environment: $environment, host_name: $host,
   service_url: $service_url, service_version: {major: $service_major, minor: $service_minor},
   backend_name: $backend, pool_name: $pool,
   operator_namespace: $operator_namespace, workflow_namespace: $workflow_namespace,
   workflow_data_uri: $workflow_data_uri,
   workload_identity: {id: $identity_id, client_id: $identity_client_id, tenant_id: $identity_tenant_id,
                       federated_credential_name: $fic_name},
   arc_cluster: {resource_id: $arc_resource_id, oidc_issuer: $arc_oidc_issuer},
   workflow_service_account: {name: $service_account, namespace: $workflow_namespace,
                              subject: $service_account_subject},
   kai_chart: {ref: $kai_ref, version: $kai_version, sha256: $kai_sha},
   backend_chart: {ref: $backend_ref, version: $backend_version, sha256: $backend_sha},
   images: {location: $image_location, version: $image_version, registry_host: $registry_host,
            manifest_file: "osmo-images.json"}}
' > "$work_dir/osmo-artifacts.json"
chmod 0600 "$work_dir/osmo-artifacts.json"

download_catalog_artifact() {
  local catalog="${1:?catalog required}" key="${2:?artifact key required}"
  local destination="${3:?destination required}" file secret version expected_sha

  file=$(jq -r --arg key "$key" '.artifacts[$key].file // empty' "$catalog")
  secret=$(jq -r --arg key "$key" '.artifacts[$key].secret_name // empty' "$catalog")
  version=$(jq -r --arg key "$key" '.artifacts[$key].secret_version // empty' "$catalog")
  expected_sha=$(jq -r --arg key "$key" '.artifacts[$key].sha256 // empty' "$catalog")
  [[ -n "$file" && -n "$secret" && -n "$version" && -n "$expected_sha" ]] || \
    fatal "Catalog has no valid artifact for $key"
  az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
    --name "$secret" --version "$version" --file "$destination" --encoding utf-8 --overwrite \
    --only-show-errors --output none
  chmod 0600 "$destination"
  [[ "$(calculate_sha256 "$destination")" == "$expected_sha" ]] || \
    fatal "Catalog artifact digest mismatch: $key"
}

existing_catalog="$work_dir/existing-catalog.json"
catalog_state="absent"
catalog_error="$work_dir/catalog-download-error.log"
if az keyvault secret download --subscription "$subscription_id" --vault-name "$vault_name" \
  --name "$catalog_secret" --file "$existing_catalog" --encoding utf-8 --overwrite \
  --only-show-errors --output none 2>"$catalog_error"; then
  chmod 0600 "$existing_catalog"
  hil_validate_catalog "$existing_catalog" "$environment" "$host_name" "$tenant_id" "$subscription_id" "$vault_name"
  hil_validate_catalog_contract "$existing_catalog" "$environment" "$host_name"
  catalog_state="valid"
elif hil_key_vault_secret_not_found "$catalog_error"; then
  rm -f "$existing_catalog"
else
  fatal "Unable to retrieve the current HiL catalog from Key Vault"
fi

token_reused=false
if [[ "$catalog_state" == "valid" ]]; then
  operation="validate current catalog token and artifact contract"
  download_catalog_artifact "$existing_catalog" osmo_token "$work_dir/osmo-token"
  download_catalog_artifact "$existing_catalog" osmo_token_metadata "$work_dir/osmo-token-metadata.json"
  download_catalog_artifact "$existing_catalog" osmo_artifacts "$work_dir/existing-osmo-artifacts.json"
  hil_validate_osmo_token_metadata_fields "$work_dir/osmo-token-metadata.json" "$work_dir/osmo-token" \
    "$environment" "$host_name" "$backend_name"
  jq -e --slurp '.[0] == .[1]' "$work_dir/existing-osmo-artifacts.json" "$work_dir/osmo-artifacts.json" >/dev/null || \
    fatal "Current catalog OSMO artifact contract is incompatible with the selected service"
  if [[ "$renew_token" == "false" ]] && hil_token_metadata_is_unexpired "$work_dir/osmo-token-metadata.json"; then
    token_reused=true
  fi
fi

if [[ "$token_reused" == "false" ]]; then
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
    --arg token_name "$token_name" --arg expiry "$token_expiry_utc" --arg sha "$token_sha" '
    {schema_version: 1, kind: "physical-ai-osmo-service-token", environment: $environment,
     host_name: $host, backend_name: $backend, token_name: $token_name, service: true,
     roles: ["osmo-backend"], expires_at: $expiry, token_sha256: $sha}
  ' > "$work_dir/osmo-token-metadata.json"
  chmod 0600 "$work_dir/osmo-token-metadata.json"
fi

operation="publish generic non-secret environment bundle"
"$SCRIPT_DIR/upload-environment-bundle.sh" --environment "$environment" --tf-dir "$tf_dir" \
  --bundle-dir "$bundle_dir" --vault-name "$vault_name"

operation="publish exact HiL artifacts"
install -m 0600 "$registry_config_file" "$work_dir/registry-config.json"
if [[ "$catalog_state" == "valid" ]]; then
  install -m 0600 "$existing_catalog" "$work_dir/catalog.json"
else
  jq -n --arg environment "$environment" --arg host "$host_name" --arg tenant "$tenant_id" \
    --arg subscription "$subscription_id" --arg vault "$vault_name" --arg csr "$csr_secret" \
    --arg response "$vpn_response_secret" '
    {schema_version: 1, kind: "physical-ai-hil-catalog", environment: $environment,
     host_name: $host, tenant_id: $tenant, subscription_id: $subscription, vault_name: $vault,
     csr_secret_name: $csr, vpn_response_secret_name: $response, artifacts: {}}
  ' > "$work_dir/catalog.json"
fi
chmod 0600 "$work_dir/catalog.json"

add_published_file() {
  local key="${1:?key required}" file="${2:?file required}" secret="${3:?secret required}"
  local content_type="${4:?content type required}" version next_catalog
  version=$(secret_version "$secret" "$file" "$content_type")
  next_catalog="$work_dir/catalog-next.json"
  catalog_add_artifact "$work_dir/catalog.json" "$next_catalog" "$key" "$(basename "$file")" \
    "$secret" "$version" "$(calculate_sha256 "$file")"
  mv "$next_catalog" "$work_dir/catalog.json"
}

deployment_version=$(az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
  --name "${environment}-deployment" --query id -o tsv)
verify_secret_version "${environment}-deployment" "${deployment_version##*/}" "$deployment_file"
deployment_version="${deployment_version##*/}"
catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" deployment deployment.json \
  "${environment}-deployment" "$deployment_version" "$(calculate_sha256 "$deployment_file")"
mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
image_version_id=$(az keyvault secret show --subscription "$subscription_id" --vault-name "$vault_name" \
  --name "${environment}-osmo-images" --query id -o tsv)
verify_secret_version "${environment}-osmo-images" "${image_version_id##*/}" "$image_manifest"
image_version_id="${image_version_id##*/}"
catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" image_manifest osmo-images.json \
  "${environment}-osmo-images" "$image_version_id" "$(calculate_sha256 "$image_manifest")"
mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
if [[ "$token_reused" == "true" ]]; then
  token_version=$(jq -r '.artifacts.osmo_token.secret_version' "$existing_catalog")
  token_metadata_version=$(jq -r '.artifacts.osmo_token_metadata.secret_version' "$existing_catalog")
  catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" osmo_token osmo-token \
    "${environment}-${host_name}-osmo-token" "$token_version" "$(calculate_sha256 "$work_dir/osmo-token")"
  mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
  catalog_add_artifact "$work_dir/catalog.json" "$work_dir/catalog-next.json" osmo_token_metadata \
    osmo-token-metadata.json "${environment}-${host_name}-osmo-token-metadata" \
    "$token_metadata_version" "$(calculate_sha256 "$work_dir/osmo-token-metadata.json")"
  mv "$work_dir/catalog-next.json" "$work_dir/catalog.json"
else
  add_published_file osmo_token "$work_dir/osmo-token" "${environment}-${host_name}-osmo-token" text/plain
  add_published_file osmo_token_metadata "$work_dir/osmo-token-metadata.json" \
    "${environment}-${host_name}-osmo-token-metadata" application/json
fi
add_published_file registry_config "$work_dir/registry-config.json" \
  "${environment}-${host_name}-registry-config" application/json
add_published_file osmo_artifacts "$work_dir/osmo-artifacts.json" \
  "${environment}-${host_name}-osmo-artifacts" application/json

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
fi

operation="publish completion catalog last"
secret_version "$catalog_secret" "$work_dir/catalog.json" application/json true >/dev/null
catalog_activated=true

trap - ERR
section "Deployment Summary"
print_kv "Environment" "$environment"
print_kv "Host" "$host_name"
print_kv "Backend" "$backend_name"
print_kv "Pool" "$pool_name"
print_kv "Workflow Data URI" "$workflow_data_uri"
print_kv "OSMO Identity Client ID" "$osmo_identity_client_id"
print_kv "Arc Cluster" "$arc_cluster_resource_id"
print_kv "Arc OIDC Issuer" "$arc_oidc_issuer"
print_kv "Key Vault" "$vault_name"
print_kv "Catalog" "published last"
print_kv "Token" "$([[ $token_reused == true ]] && echo 'reused' || echo 'issued')"
print_kv "VPN Inputs" "$([[ -n $vpn_input_dir ]] && echo published || echo 'not requested')"
print_kv "RBAC" "unchanged; verify individual-secret assignments separately"
info "Trusted HiL environment inputs are published"
