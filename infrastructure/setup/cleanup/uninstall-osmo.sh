#!/usr/bin/env bash
# Uninstall OSMO control plane and backend operator, clean up resources
# cspell:ignore REDISCLI
#
# Prerequisites:
#   - AKS cluster accessible (kubectl configured)
#   - Terraform outputs available for data purge operations
#   - helm, kubectl, az, jq on PATH
#
# Usage:
#   cleanup/uninstall-osmo.sh
#   cleanup/uninstall-osmo.sh --purge-all
#   cleanup/uninstall-osmo.sh --skip-backend --skip-k8s-cleanup
#   cleanup/uninstall-osmo.sh --config-preview
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SCRIPT_DIR/../defaults.conf"

show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Uninstall OSMO control plane and backend operator, clean up resources.

OPTIONS:
    -h, --help              Show this help message
    -t, --tf-dir DIR        Terraform directory (default: $DEFAULT_TF_DIR)
    --kubeconfig PATH       Isolated AKS kubeconfig output
    --context NAME          Explicit AKS context (default: cluster name)
    --expected-aks-resource-id ID
                            Required AKS resource ID safety boundary
    --skip-backend          Skip backend operator removal
    --skip-k8s-cleanup      Skip cleaning up K8s resources
    --backend-name NAME     Backend identifier (default: default)
    --delete-container      Delete the storage container (destructive)
    --container-name NAME   Blob container name (default: osmo)
    --purge-postgres        Drop all OSMO tables from PostgreSQL (destructive)
    --purge-redis           Flush OSMO keys from Redis (destructive)
    --purge-all             Enable all purge/delete options (destructive)
    --db-name NAME          PostgreSQL database name (default: osmo)
    --use-local-osmo        Use local osmo-dev CLI instead of production osmo
    --config-preview        Print configuration and exit

EXAMPLES:
    $(basename "$0")
    $(basename "$0") --skip-backend
    $(basename "$0") --purge-postgres --purge-redis
    $(basename "$0") --delete-container
EOF
}

tf_dir="$SCRIPT_DIR/../$DEFAULT_TF_DIR"
kubeconfig=""
kubeconfig_set=false
context=""
expected_aks_resource_id=""
skip_backend=false
skip_k8s_cleanup=false
backend_name="default"
delete_container=false
container_name="osmo"
purge_postgres=false
purge_redis=false
db_name="osmo"
use_local_osmo=false
config_preview=false
postgres_image="postgres:16@sha256:eb4759788a2182f08257135e61a34f2cfc3c2914079f3465d64ee62350f4d081"
redis_image="redis:7@sha256:a8f08480e1f88f2647fed492d1178c06abb0d0c1fbf02c682a61e2f483fb3954"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)            show_help; exit 0 ;;
        -t|--tf-dir)          tf_dir="$2"; shift 2 ;;
        --kubeconfig)         kubeconfig="$2"; kubeconfig_set=true; shift 2 ;;
        --context)            context="$2"; shift 2 ;;
        --expected-aks-resource-id) expected_aks_resource_id="$2"; shift 2 ;;
        --skip-backend)       skip_backend=true; shift ;;
        --skip-k8s-cleanup)   skip_k8s_cleanup=true; shift ;;
        --backend-name)       backend_name="$2"; shift 2 ;;
        --delete-container)   delete_container=true; shift ;;
        --container-name)     container_name="$2"; shift 2 ;;
        --purge-all)          delete_container=true; purge_postgres=true; purge_redis=true; shift ;;
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
if [[ "$kubeconfig_set" == "true" && -z "$expected_aks_resource_id" ]]; then
    fatal "--expected-aks-resource-id is required with --kubeconfig"
fi
expected_aks_resource_id="${expected_aks_resource_id:-$(tf_require "$tf_output" "aks_cluster.value.id" "AKS cluster resource ID")}"
verify_aks_resource_id "$tf_output" "$expected_aks_resource_id"

cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster name")
rg=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
kubeconfig="${kubeconfig:-$HOME/.kube/physical-ai-toolchain/${cluster}.yaml}"
context="${context:-$cluster}"
verify_existing_aks_kubeconfig "$kubeconfig" "$context" "$expected_aks_resource_id"
keyvault=$(tf_get "$tf_output" "key_vault_name.value")
pg_fqdn=$(tf_get "$tf_output" "postgresql_connection_info.value.fqdn")
pg_user=$(tf_get "$tf_output" "postgresql_connection_info.value.admin_username")
redis_hostname=$(tf_get "$tf_output" "managed_redis_connection_info.value.hostname")
redis_port=$(tf_get "$tf_output" "managed_redis_connection_info.value.port" "10000")
storage_name=$(tf_get "$tf_output" "storage_account.value.name")

if [[ "$config_preview" == "true" ]]; then
    section "Configuration Preview"
    print_kv "Cluster" "$cluster"
    print_kv "Kubeconfig" "$kubeconfig"
    print_kv "Context" "$context"
    print_kv "Resource Group" "$rg"
    print_kv "AKS Resource ID" "$expected_aks_resource_id"
    print_kv "Control Plane NS" "$NS_OSMO_CONTROL_PLANE"
    print_kv "Backend" "$([[ $skip_backend == true ]] && echo 'skipped' || echo "$backend_name")"
    print_kv "PostgreSQL" "${pg_fqdn:-<not configured>}"
    print_kv "Redis" "${redis_hostname:-<not configured>}:${redis_port}"
    print_kv "Storage Account" "${storage_name:-<not configured>}"
    print_kv "Database Name" "$db_name"
    print_kv "Delete Container" "$delete_container"
    print_kv "Purge PostgreSQL" "$purge_postgres"
    print_kv "Purge Redis" "$purge_redis"
    exit 0
fi

#------------------------------------------------------------------------------
# Connect to Cluster
#------------------------------------------------------------------------------
section "Connect to Cluster"

connect_aks "$rg" "$cluster" "$kubeconfig" "$context"

cleanup_purge_resources() {
    kubectl delete pod osmo-purge-db osmo-purge-redis -n default --ignore-not-found >/dev/null 2>&1 || true
    kubectl delete secret osmo-purge-db osmo-purge-redis -n default --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup_purge_resources EXIT

#------------------------------------------------------------------------------
# Destructive Preflight
#------------------------------------------------------------------------------

pg_password=""
redis_key=""

if [[ "$purge_postgres" == "true" ]]; then
    section "Preflight PostgreSQL Purge"
    [[ -n "$pg_fqdn" && -n "$pg_user" && -n "$keyvault" ]] || \
        fatal "PostgreSQL or Key Vault is not configured; refusing incomplete purge"
    pg_password=$(az keyvault secret show --vault-name "$keyvault" --name "psql-admin-password" --query value -o tsv)
    [[ -n "$pg_password" ]] || fatal "Could not retrieve PostgreSQL password; refusing incomplete purge"
    kubectl create secret generic osmo-purge-db -n default \
        --from-file=password=<(printf '%s' "$pg_password") \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
        kubectl delete pod osmo-purge-db -n default --ignore-not-found >/dev/null 2>&1
        jq -n --arg image "$postgres_image" \
                --arg connection "host=$pg_fqdn port=5432 dbname=$db_name user=$pg_user sslmode=require" '
                {
                    apiVersion: "v1",
                    kind: "Pod",
                    metadata: {name: "osmo-purge-db", namespace: "default"},
                    spec: {
                        restartPolicy: "Never",
                        containers: [{
                            name: "psql",
                            image: $image,
                            env: [{
                                name: "PGPASSWORD",
                                valueFrom: {secretKeyRef: {name: "osmo-purge-db", key: "password"}}
                            }],
                            command: ["psql"],
                            args: [$connection, "-c", "SELECT 1"]
                        }]
                    }
                }
        ' | kubectl apply -f - >/dev/null
    if ! kubectl wait pod/osmo-purge-db -n default \
        --for=jsonpath='{.status.phase}'=Succeeded --timeout=120s >/dev/null 2>&1; then
        kubectl logs pod/osmo-purge-db -n default >&2 || true
        fatal "PostgreSQL purge preflight failed"
    fi
    kubectl delete pod osmo-purge-db -n default --ignore-not-found >/dev/null
    kubectl delete secret osmo-purge-db -n default --ignore-not-found >/dev/null
fi

if [[ "$purge_redis" == "true" ]]; then
    section "Preflight Redis Purge"
    [[ -n "$redis_hostname" && -n "$keyvault" ]] || \
        fatal "Redis or Key Vault is not configured; refusing incomplete purge"
    redis_key=$(az keyvault secret show --vault-name "$keyvault" --name "redis-primary-key" --query value -o tsv)
    [[ -n "$redis_key" ]] || fatal "Could not retrieve Redis access key; refusing incomplete purge"
    kubectl create secret generic osmo-purge-redis -n default \
        --from-file=password=<(printf '%s' "$redis_key") \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
        kubectl delete pod osmo-purge-redis -n default --ignore-not-found >/dev/null 2>&1
        jq -n --arg image "$redis_image" --arg host "$redis_hostname" --arg port "$redis_port" '
                {
                    apiVersion: "v1",
                    kind: "Pod",
                    metadata: {name: "osmo-purge-redis", namespace: "default"},
                    spec: {
                        restartPolicy: "Never",
                        containers: [{
                            name: "redis-cli",
                            image: $image,
                            env: [{
                                name: "REDISCLI_AUTH",
                                valueFrom: {secretKeyRef: {name: "osmo-purge-redis", key: "password"}}
                            }],
                            command: ["redis-cli"],
                            args: ["-h", $host, "-p", $port, "--tls", "--insecure", "--no-auth-warning", "PING"]
                        }]
                    }
                }
        ' | kubectl apply -f - >/dev/null
    if ! kubectl wait pod/osmo-purge-redis -n default \
        --for=jsonpath='{.status.phase}'=Succeeded --timeout=120s >/dev/null 2>&1; then
        kubectl logs pod/osmo-purge-redis -n default >&2 || true
        fatal "Redis purge preflight failed"
    fi
    kubectl delete pod osmo-purge-redis -n default --ignore-not-found >/dev/null
    kubectl delete secret osmo-purge-redis -n default --ignore-not-found >/dev/null
fi

#------------------------------------------------------------------------------
# Uninstall Backend Operator
#------------------------------------------------------------------------------

if [[ "$skip_backend" == "true" ]]; then
    info "Skipping backend operator removal (--skip-backend)"
else
    section "Uninstall Backend Operator"

    if helm status osmo-operator -n "$NS_OSMO_OPERATOR" &>/dev/null; then
        info "Uninstalling osmo-operator Helm release..."
        helm uninstall osmo-operator -n "$NS_OSMO_OPERATOR" --wait --timeout "$TIMEOUT_DEPLOY"
    else
        info "Helm release 'osmo-operator' not found, skipping..."
    fi

    if [[ "$skip_k8s_cleanup" == "false" ]]; then
        for resource in "secret/svc-osmo-admin" "configmap/incluster-kubeconfig"; do
            if kubectl get "$resource" -n "$NS_OSMO_OPERATOR" &>/dev/null; then
                info "Deleting $resource..."
                kubectl delete "$resource" -n "$NS_OSMO_OPERATOR" --ignore-not-found
            fi
        done

        if kubectl get serviceaccount "$WORKFLOW_SERVICE_ACCOUNT" -n "$NS_OSMO_WORKFLOWS" &>/dev/null; then
            info "Deleting workflow ServiceAccount..."
            kubectl delete serviceaccount "$WORKFLOW_SERVICE_ACCOUNT" -n "$NS_OSMO_WORKFLOWS" --ignore-not-found
        fi

        for ns in "$NS_OSMO_WORKFLOWS" "$NS_OSMO_OPERATOR"; do
            if kubectl get namespace "$ns" &>/dev/null; then
                info "Deleting namespace '$ns'..."
                kubectl delete namespace "$ns" --ignore-not-found --timeout=60s || true
            fi
        done
    fi
fi

#------------------------------------------------------------------------------
# Uninstall Control Plane
#------------------------------------------------------------------------------
section "Uninstall Control Plane"

if helm status osmo -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    info "Uninstalling 'osmo' Helm release..."
    helm uninstall osmo -n "$NS_OSMO_CONTROL_PLANE" --wait --timeout "$TIMEOUT_DEPLOY"
else
    info "Release 'osmo' not found, skipping..."
fi

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
# Delete Storage Container
#------------------------------------------------------------------------------

if [[ "$delete_container" == "true" ]]; then
    section "Delete Storage Container"

    if [[ -z "$storage_name" ]]; then
        warn "Storage account not found in terraform outputs, skipping..."
    elif az storage container show --account-name "$storage_name" --name "$container_name" --auth-mode login &>/dev/null; then
        warn "Deleting container '$container_name' (this will delete all workflow data)..."
        az storage container delete --account-name "$storage_name" --name "$container_name" --auth-mode login
    else
        info "Container '$container_name' not found, skipping..."
    fi
else
    info "Skipping container deletion (use --delete-container to remove)"
fi

#------------------------------------------------------------------------------
# Purge PostgreSQL Data
#------------------------------------------------------------------------------

if [[ "$purge_postgres" == "true" ]]; then
    section "Purge PostgreSQL Data"

        warn "Dropping all tables from database '$db_name' (public schema)..."

        drop_sql="SET client_min_messages TO WARNING; DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO PUBLIC;"

        kubectl delete pod osmo-purge-db -n default --ignore-not-found >/dev/null 2>&1
        kubectl create secret generic osmo-purge-db -n default \
                --from-file=password=<(printf '%s' "$pg_password") \
                --dry-run=client -o yaml | kubectl apply -f - >/dev/null
        jq -n --arg image "$postgres_image" \
                --arg connection "host=$pg_fqdn port=5432 dbname=$db_name user=$pg_user sslmode=require" \
                --arg sql "$drop_sql" '
                {
                    apiVersion: "v1",
                    kind: "Pod",
                    metadata: {name: "osmo-purge-db", namespace: "default"},
                    spec: {
                        restartPolicy: "Never",
                        containers: [{
                            name: "psql",
                            image: $image,
                            env: [{
                                name: "PGPASSWORD",
                                valueFrom: {secretKeyRef: {name: "osmo-purge-db", key: "password"}}
                            }],
                            command: ["psql"],
                            args: [$connection, "-c", $sql]
                        }]
                    }
                }
        ' | kubectl apply -f - >/dev/null
    if kubectl wait pod/osmo-purge-db -n default \
        --for=jsonpath='{.status.phase}'=Succeeded --timeout=120s >/dev/null 2>&1; then
        info "PostgreSQL public schema dropped and recreated"
    else
        kubectl logs pod/osmo-purge-db -n default >&2 || true
        fatal "Failed to drop PostgreSQL schema"
    fi
    kubectl delete pod osmo-purge-db -n default --ignore-not-found >/dev/null 2>&1
    kubectl delete secret osmo-purge-db -n default --ignore-not-found >/dev/null 2>&1
else
    info "Skipping PostgreSQL purge (use --purge-postgres to remove data)"
fi

#------------------------------------------------------------------------------
# Purge Redis Data
#------------------------------------------------------------------------------

if [[ "$purge_redis" == "true" ]]; then
    section "Purge Redis Data"

    warn "Flushing OSMO keys from Redis..."

    flush_script='local keys = redis.call("KEYS", "{osmo}:*"); for i=1,#keys do redis.call("DEL", keys[i]) end; return #keys'

    kubectl delete pod osmo-purge-redis -n default --ignore-not-found >/dev/null 2>&1
    kubectl create secret generic osmo-purge-redis -n default \
        --from-file=password=<(printf '%s' "$redis_key") \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
        jq -n --arg image "$redis_image" --arg host "$redis_hostname" --arg port "$redis_port" \
                --arg script "$flush_script" '
                {
                    apiVersion: "v1",
                    kind: "Pod",
                    metadata: {name: "osmo-purge-redis", namespace: "default"},
                    spec: {
                        restartPolicy: "Never",
                        containers: [{
                            name: "redis-cli",
                            image: $image,
                            env: [{
                                name: "REDISCLI_AUTH",
                                valueFrom: {secretKeyRef: {name: "osmo-purge-redis", key: "password"}}
                            }],
                            command: ["redis-cli"],
                            args: ["-h", $host, "-p", $port, "--tls", "--insecure", "--no-auth-warning", "EVAL", $script, "0"]
                        }]
                    }
                }
        ' | kubectl apply -f - >/dev/null
    if kubectl wait pod/osmo-purge-redis -n default \
        --for=jsonpath='{.status.phase}'=Succeeded --timeout=120s >/dev/null 2>&1; then
        info "Redis keys flushed"
    else
        kubectl logs pod/osmo-purge-redis -n default >&2 || true
        fatal "Failed to flush Redis keys"
    fi
    kubectl delete pod osmo-purge-redis -n default --ignore-not-found >/dev/null 2>&1
    kubectl delete secret osmo-purge-redis -n default --ignore-not-found >/dev/null 2>&1
else
    info "Skipping Redis purge (use --purge-redis to remove data)"
fi

#------------------------------------------------------------------------------
# Verification
#------------------------------------------------------------------------------
section "Verification"
verification_failed=false

if helm status osmo -n "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    error "Helm release 'osmo' still exists"
    verification_failed=true
else
    info "Helm release 'osmo' removed"
fi

if [[ "$skip_backend" == "false" ]]; then
    if helm status osmo-operator -n "$NS_OSMO_OPERATOR" &>/dev/null; then
        error "Helm release 'osmo-operator' still exists"
        verification_failed=true
    else
        info "Helm release 'osmo-operator' removed"
    fi
fi

if kubectl get namespace "$NS_OSMO_CONTROL_PLANE" &>/dev/null; then
    error "$NS_OSMO_CONTROL_PLANE namespace still exists"
    verification_failed=true
else
    info "$NS_OSMO_CONTROL_PLANE namespace removed"
fi

if [[ "$skip_backend" == "false" ]]; then
    for ns in "$NS_OSMO_OPERATOR" "$NS_OSMO_WORKFLOWS"; do
        if kubectl get namespace "$ns" &>/dev/null; then
            error "$ns namespace still exists"
            verification_failed=true
        else
            info "$ns namespace removed"
        fi
    done
fi

[[ "$verification_failed" == "false" ]] || fatal "OSMO cleanup verification failed"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Uninstall Summary"
print_kv "Cluster" "$cluster"
print_kv "Resource Group" "$rg"
print_kv "Control Plane" "uninstalled"
print_kv "Backend" "$([[ $skip_backend == true ]] && echo 'skipped' || echo 'uninstalled')"
print_kv "Storage Container" "$([[ $delete_container == true ]] && echo 'deleted' || echo 'preserved')"
print_kv "PostgreSQL" "$([[ $purge_postgres == true ]] && echo 'purged' || echo 'preserved')"
print_kv "Redis" "$([[ $purge_redis == true ]] && echo 'purged' || echo 'preserved')"
echo
info "To reinstall, run: ../03-deploy-osmo.sh"

info "OSMO uninstall complete"
