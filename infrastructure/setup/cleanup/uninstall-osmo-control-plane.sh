#!/usr/bin/env bash
# Uninstall OSMO Control Plane components and clean up resources
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Uninstall OSMO Control Plane components (service, router, web-ui) and clean up resources.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --skip-osmo-config      Skip removing OSMO configurations
    --skip-k8s-cleanup      Skip cleaning up K8s resources
    --delete-mek            Delete MEK ConfigMap (data loss warning)
    --purge-postgres        Drop all OSMO tables from PostgreSQL (destructive)
    --purge-redis           Flush OSMO keys from Redis (destructive)
    --db-name NAME          PostgreSQL database name (default: osmo)
    --use-local-osmo        Use local osmo-dev CLI instead of production osmo
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --skip-osmo-config
    $(basename "$0") --delete-mek --purge-postgres --purge-redis
EOF
}

tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
skip_osmo_config=false
skip_k8s_cleanup=false
delete_mek=false
purge_postgres=false
purge_redis=false
db_name="osmo"
use_local_osmo=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -t|--tf-dir)          tf_dir="$2"; shift 2 ;;
    --skip-osmo-config)   skip_osmo_config=true; shift ;;
    --skip-k8s-cleanup)   skip_k8s_cleanup=true; shift ;;
    --delete-mek)         delete_mek=true; shift ;;
    --purge-postgres)     purge_postgres=true; shift ;;
    --purge-redis)        purge_redis=true; shift ;;
    --db-name)            db_name="$2"; shift 2 ;;
    --use-local-osmo)     use_local_osmo=true; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools az terraform kubectl helm jq

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

info "Reading terraform outputs from $tf_dir..."
tf_output=$(read_terraform_outputs "$tf_dir")

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
keyvault=$(tf_get "$tf_output" "key_vault_name.value")
pg_fqdn=$(tf_get "$tf_output" "postgresql_connection_info.value.fqdn")
pg_user=$(tf_get "$tf_output" "postgresql_connection_info.value.admin_username")
redis_hostname=$(tf_get "$tf_output" "managed_redis_connection_info.value.hostname")

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Cluster" "$cluster"
  print_kv "Resource Group" "$rg"
  print_kv "Namespace" "$NS_OSMO_CONTROL_PLANE"
  print_kv "PostgreSQL" "${pg_fqdn:-<not configured>}"
  print_kv "Redis" "${redis_hostname:-<not configured>}"
  print_kv "Database Name" "$db_name"
  print_kv "Delete MEK" "$delete_mek"
  print_kv "Purge PostgreSQL" "$purge_postgres"
  print_kv "Purge Redis" "$purge_redis"
  exit 0
fi

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster"

#------------------------------------------------------------------------------
# Remove OSMO Configurations
#------------------------------------------------------------------------------

if [[ "$skip_osmo_config" == "true" ]]; then
  info "Skipping OSMO config removal (--skip-osmo-config)"
elif ! command -v osmo &>/dev/null; then
  warn "osmo CLI not found, skipping configuration cleanup"
elif osmo config get SERVICE &>/dev/null 2>&1; then
  section "Remove OSMO Configurations"
  info "Removing OSMO SERVICE config..."
  osmo config delete SERVICE 2>/dev/null || true
else
  info "OSMO SERVICE config not found, skipping..."
fi

#------------------------------------------------------------------------------
# Uninstall Helm Releases
#------------------------------------------------------------------------------
section "Uninstall Helm Releases"

for release in "ui" "router" "service"; do
  if helm status "$release" -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    info "Uninstalling '$release'..."
    helm uninstall "$release" -n "$NS_OSMO_CONTROL_PLANE" --wait --timeout "$TIMEOUT_DEPLOY"
  else
    info "Release '$release' not found, skipping..."
  fi
done

#------------------------------------------------------------------------------
# Cleanup Kubernetes Resources
#------------------------------------------------------------------------------

if [[ "$skip_k8s_cleanup" == "true" ]]; then
  info "Skipping K8s cleanup (--skip-k8s-cleanup)"
else
  section "Cleanup Kubernetes Resources"

  if kubectl get secretproviderclass azure-keyvault-secrets -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    info "Deleting SecretProviderClass 'azure-keyvault-secrets'..."
    kubectl delete secretproviderclass azure-keyvault-secrets -n "$NS_OSMO_CONTROL_PLANE" --ignore-not-found
  fi

  for secret in "$SECRET_POSTGRES" "$SECRET_REDIS"; do
    if kubectl get secret "$secret" -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
      info "Deleting secret '$secret'..."
      kubectl delete secret "$secret" -n "$NS_OSMO_CONTROL_PLANE" --ignore-not-found
    fi
  done

  if kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    if [[ "$delete_mek" == "true" ]]; then
      warn "Deleting MEK ConfigMap (encrypted data will be unrecoverable)..."
      kubectl delete configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" --ignore-not-found
    else
      warn "MEK ConfigMap preserved (use --delete-mek to remove)"
    fi
  fi

  if kubectl get svc azureml-ingress-nginx-internal-lb -n azureml &>/dev/null; then
    info "Deleting internal LB ingress service..."
    kubectl delete svc azureml-ingress-nginx-internal-lb -n azureml --ignore-not-found || true
  fi

  if kubectl get namespace "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    info "Deleting namespace '$NS_OSMO_CONTROL_PLANE'..."
    kubectl delete namespace "$NS_OSMO_CONTROL_PLANE" --ignore-not-found --timeout=60s || true
  fi
fi

#------------------------------------------------------------------------------
# Purge PostgreSQL Data
#------------------------------------------------------------------------------

if [[ "$purge_postgres" == "true" ]]; then
  section "Purge PostgreSQL Data"
  require_tools psql

  if [[ -z "$pg_fqdn" || -z "$keyvault" ]]; then
    warn "PostgreSQL or Key Vault not configured, skipping..."
  else
    pg_password=$(az keyvault secret show --vault-name "$keyvault" --name "psql-admin-password" --query value -o tsv 2>/dev/null) || true

    if [[ -z "$pg_password" ]]; then
      warn "Could not retrieve PostgreSQL password, skipping..."
    else
      warn "Dropping all OSMO tables from database '$db_name'..."

      # OSMO tables in dependency order (children first, then parents)
      osmo_tables=(
        "collection" "dataset_tag" "dataset_version" "dataset"
        "credential" "access_token" "profile" "config_history" "backend_tests"
        "resource_platforms" "resources" "app_versions" "apps"
        "task_io" "tasks" "groups" "workflow_tags" "workflows"
        "pools" "pod_templates" "resource_validations" "backends" "roles" "configs" "ueks"
      )

      drop_sql="SET client_min_messages TO WARNING;"
      for t in "${osmo_tables[@]}"; do
        drop_sql+="DROP TABLE IF EXISTS $t CASCADE;"
      done
      drop_sql+="DROP TYPE IF EXISTS credential_type CASCADE;"
      drop_sql+="DROP FUNCTION IF EXISTS jsonb_recursive_merge(jsonb, jsonb) CASCADE;"

      if PGPASSWORD="$pg_password" psql \
          "host=$pg_fqdn port=5432 dbname=$db_name user=$pg_user sslmode=require" \
          -c "$drop_sql" 2>/dev/null; then
        info "PostgreSQL tables dropped"
      else
        warn "Failed to drop PostgreSQL tables"
      fi
    fi
  fi
else
  info "Skipping PostgreSQL purge (use --purge-postgres to remove data)"
fi

#------------------------------------------------------------------------------
# Purge Redis Data
#------------------------------------------------------------------------------

if [[ "$purge_redis" == "true" ]]; then
  section "Purge Redis Data"
  require_tools redis-cli

  if [[ -z "$redis_hostname" || -z "$keyvault" ]]; then
    warn "Redis or Key Vault not configured, skipping..."
  else
    redis_key=$(az keyvault secret show --vault-name "$keyvault" --name "redis-primary-key" --query value -o tsv 2>/dev/null) || true

    if [[ -z "$redis_key" ]]; then
      warn "Could not retrieve Redis access key, skipping..."
    else
      warn "Flushing OSMO keys from Redis..."

      if redis-cli -h "$redis_hostname" -p 6380 -a "$redis_key" --tls --no-auth-warning <<-'REDIS' 2>/dev/null
EVAL "local keys = redis.call('KEYS', '{osmo}:*'); for i=1,#keys do redis.call('DEL', keys[i]) end; return #keys" 0
DEL delayed_job_queue
REDIS
      then
        info "Redis keys flushed"
      else
        warn "Failed to flush Redis keys"
      fi
    fi
  fi
else
  info "Skipping Redis purge (use --purge-redis to remove data)"
fi

#------------------------------------------------------------------------------
# Verification
#------------------------------------------------------------------------------
section "Verification"

for release in "service" "router" "ui"; do
  if helm status "$release" -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    warn "Helm release '$release' still exists"
  else
    info "Helm release '$release' removed"
  fi
done

if kubectl get namespace "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
  warn "$NS_OSMO_CONTROL_PLANE namespace still exists (may be terminating)"
else
  info "$NS_OSMO_CONTROL_PLANE namespace removed"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Uninstall Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "Namespace" "$NS_OSMO_CONTROL_PLANE"
print_kv "MEK ConfigMap" "$([[ $delete_mek == true ]] && echo 'deleted' || echo 'preserved')"
print_kv "PostgreSQL" "$([[ $purge_postgres == true ]] && echo 'purged' || echo 'preserved')"
print_kv "Redis" "$([[ $purge_redis == true ]] && echo 'purged' || echo 'preserved')"
echo
info "To reinstall, run: ../03-deploy-osmo-control-plane.sh"

info "OSMO Control Plane uninstall complete"
