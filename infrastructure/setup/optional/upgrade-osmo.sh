#!/usr/bin/env bash
# Upgrade a pre-6.3 OSMO install to the OSMO 6.3 ConfigMap deployment in confirmed, resumable stages
#
# Data-keeping path: backup -> hop-6.2 -> tokens -> export -> hop-6.3 -> verify
# Fresh path:        backup -> reset -> hop-6.3 -> verify
# cspell:ignore fromdateiso pgroll rtrimstr schemaname slurpfile
set -o errexit -o nounset -o pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../defaults.conf
source "$SETUP_DIR/defaults.conf"

STAGES=(backup hop-6.2 tokens export reset hop-6.3 verify)
CONFIG_TYPES=(service workflow dataset backend pool pod_template resource_validation group_template backend_test role)
REQUIRED_CONFIG_TYPES=(service workflow backend pool pod_template)
OSMO_API_USER="admin"
PYYAML_VERSION="6.0.3"
EXPORTER_URL="https://raw.githubusercontent.com/NVIDIA/OSMO/${OSMO_UPGRADE_TOOLS_COMMIT}/deployments/upgrades/export_configs_to_helm.py"

show_help() {
    cat << EOF
Usage: $(basename "$0") --stage STAGE --backup-dir PATH [OPTIONS] [-- 03-deploy-osmo.sh OPTIONS]

Upgrade a pre-6.3 OSMO install (separate service, router, and web-ui releases with
database config) to OSMO $OSMO_IMAGE_VERSION in ConfigMap mode, one stage per run.
Each stage checks that the stage before it succeeded.

Data-keeping path:  backup -> hop-6.2 -> tokens -> export -> hop-6.3 -> verify
Fresh path:         backup -> reset -> hop-6.3 -> verify

STAGES:
    backup     Dump the OSMO database; save the MEK, Helm values, operator token, and configs
    hop-6.2    Upgrade the legacy releases to chart $OSMO_UPGRADE_62_CHART_VERSION (OSMO $OSMO_UPGRADE_62_IMAGE_VERSION) and migrate the database
    tokens     Create the backend-operator user and token; update the backend operator
    export     Export the database configs to Helm values in the environment bundle
    reset      Remove the legacy releases; purge OSMO's PostgreSQL tables and Redis keys
    hop-6.3    Remove the legacy releases, then install OSMO $OSMO_IMAGE_VERSION with 03-deploy-osmo.sh
    verify     Check the version, configured objects, backend status, and ConfigMap mode

REQUIRED:
    --stage STAGE               Stage to run
    --backup-dir PATH           Absolute directory outside the repository and bundle roots
                                for backups, exports, and stage state (created with mode 700)

OPTIONS:
    -h, --help                  Show this help message
    -t, --tf-dir DIR            Terraform directory (default: $DEFAULT_TF_DIR)
    --kubeconfig PATH           Isolated AKS kubeconfig (default: ~/.kube/physical-ai-toolchain/<cluster>.yaml)
    --context NAME              AKS context (default: cluster name)
    --expected-aks-resource-id ID
                                Required with --kubeconfig
    --bundle-dir PATH           Environment bundle; export writes osmo-platforms.yaml there
    --platform-values PATH      Reviewed values for hop-6.3 (default: <bundle-dir>/osmo-platforms.yaml)
    --service-url URL           OSMO service URL (default: detected internal ingress)
    --backend-name NAME         AKS backend name (default: default)
    --token-expiry YYYY-MM-DD   Backend operator token expiry (required for tokens)
    --config-preview            Print configuration and exit
    -- ARGS                     hop-6.3 only: extra 03-deploy-osmo.sh options

Confirmation prompts read the AKS cluster name from standard input.

EXAMPLES:
    $(basename "$0") --stage backup --backup-dir ~/osmo-upgrade
    $(basename "$0") --stage tokens --backup-dir ~/osmo-upgrade --token-expiry 2027-03-31
    $(basename "$0") --stage export --backup-dir ~/osmo-upgrade --bundle-dir generated/dev
    $(basename "$0") --stage hop-6.3 --backup-dir ~/osmo-upgrade --bundle-dir generated/dev -- --use-acr
EOF
}

stage=""
backup_dir=""
bundle_dir=""
platform_values=""
tf_dir="$SETUP_DIR/$DEFAULT_TF_DIR"
kubeconfig=""
kubeconfig_set=false
context=""
expected_aks_resource_id=""
service_url=""
backend_name="default"
token_expiry=""
config_preview=false
deploy_args=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)                  show_help; exit 0 ;;
        --stage)                    stage="$2"; shift 2 ;;
        --backup-dir)               backup_dir="$2"; shift 2 ;;
        --bundle-dir)               bundle_dir="$2"; shift 2 ;;
        --platform-values)          platform_values="$2"; shift 2 ;;
        -t|--tf-dir)                tf_dir="$2"; shift 2 ;;
        --kubeconfig)               kubeconfig="$2"; kubeconfig_set=true; shift 2 ;;
        --context)                  context="$2"; shift 2 ;;
        --expected-aks-resource-id) expected_aks_resource_id="$2"; shift 2 ;;
        --service-url)              service_url="${2%/}"; shift 2 ;;
        --backend-name)             backend_name="$2"; shift 2 ;;
        --token-expiry)             token_expiry="$2"; shift 2 ;;
        --config-preview)           config_preview=true; shift ;;
        --)                         shift; deploy_args=("$@"); break ;;
        *)                          fatal "Unknown option: $1" ;;
    esac
done

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

absolute_path() {
    local path="$1"
    [[ "$path" == /* ]] || path="$PWD/$path"
    normalize_absolute_path "$path"
}

is_required_config() {
    local required
    for required in "${REQUIRED_CONFIG_TYPES[@]}"; do
        [[ "$required" == "$1" ]] && return 0
    done
    return 1
}

run_python() {
    uv run --no-project --managed-python --python 3.12 --quiet --with "pyyaml==$PYYAML_VERSION" python "$@"
}

yaml_to_json() {
    run_python -c 'import json, sys, yaml; json.dump(yaml.safe_load(open(sys.argv[1], encoding="utf-8")), sys.stdout)' "$1"
}

# Run the osmo CLI with a private profile so the operator's own login stays untouched.
osmo_cli() {
    XDG_CONFIG_HOME="$work_dir/osmo/config" XDG_STATE_HOME="$work_dir/osmo/state" \
        XDG_CACHE_HOME="$work_dir/osmo/cache" XDG_DATA_HOME="$work_dir/osmo/data" command osmo "$@"
}

require_service_url() {
    [[ -n "$service_url" ]] || service_url=$(detect_service_url "$kubeconfig" "$context")
    [[ -n "$service_url" ]] || fatal "Could not detect the OSMO service URL; pass --service-url"
}

# Write the response body to a file and print the HTTP status (000 when unreachable).
api_request() {
    local method="$1" path="$2" output="$3" body="${4:-}"
    local args=(-sS --connect-timeout 10 --max-time 120 -X "$method" -o "$output" -w '%{http_code}'
        -H "x-osmo-user: $OSMO_API_USER")
    [[ -z "$body" ]] || args+=(-H "Content-Type: application/json" --data-binary "$body")
    curl "${args[@]}" "$service_url/$path" || true
}

fetch_config() {
    local path="api/configs/$1"
    [[ "$1" != "pool" ]] || path="$path?verbose=true"
    api_request GET "$path" "$2"
}

service_version() {
    local output="$work_dir/version.json" code
    code=$(api_request GET api/version "$output")
    [[ "$code" == "200" ]] || fatal "OSMO API unreachable at $service_url (HTTP $code)"
    jq -r '"\(.major).\(.minor).\(.revision)"' "$output"
}

wait_backend_online() {
    local name="$1" timeout="$2" output="$work_dir/backends.json" deadline code
    deadline=$((SECONDS + timeout))
    while true; do
        code=$(api_request GET api/configs/backend "$output")
        if [[ "$code" == "200" ]] && jq -e --arg name "$name" \
            'any(.backends[]?; .name == $name and .online == true)' "$output" >/dev/null; then
            return 0
        fi
        (( SECONDS < deadline )) || return 1
        sleep 15
    done
}

# Object names from API responses saved as <dir>/{pool,pod_template,backend}.json
api_object_names() {
    jq -n --slurpfile pool "$1/pool.json" --slurpfile templates "$1/pod_template.json" \
        --slurpfile backends "$1/backend.json" '{
            pools: ($pool[0].pools // {} | keys),
            platforms: [$pool[0].pools // {} | to_entries[] | .key as $p | (.value.platforms // {}) | keys[] | "\($p)/\(.)"],
            pod_templates: ($templates[0] // {} | keys),
            backends: [$backends[0].backends[]?.name]
        }'
}

# Object names defined under services.configs in a values file converted to JSON
values_object_names() {
    jq '(.services.configs // {}) as $c | {
            pools: ($c.pools // {} | keys),
            platforms: [$c.pools // {} | to_entries[] | .key as $p | (.value.platforms // {}) | keys[] | "\($p)/\(.)"],
            pod_templates: ($c.podTemplates // {} | keys),
            backends: ($c.backends // {} | keys)
        }' "$1"
}

# Print "<kind>: <name>" for each expected object that's absent from the actual set.
missing_objects() {
    jq -rn --argjson want "$1" --argjson have "$2" '
        ["pools", "platforms", "pod_templates", "backends"][] as $kind
        | ($want[$kind] - $have[$kind])[] | "\($kind): \(.)"'
}

describe_releases() {
    if [[ -n "$1" ]]; then
        awk -F'\t' 'NF {printf "%s%s (%s)", (n++ ? ", " : ""), $1, $2} END {print ""}' <<< "$1"
    else
        echo "none"
    fi
}

release_names_json() {
    cut -f1 <<< "$1" | jq -R . | jq -s 'map(select(length > 0))'
}

chart_line() {
    local version="${1%%-*}"
    printf '%s\n' "${version%.*}"
}

line_older_than() {
    local a_major="${1%%.*}" a_minor="${1#*.}" b_major="${2%%.*}" b_minor="${2#*.}"
    (( a_major < b_major || (a_major == b_major && a_minor < b_minor) ))
}

# Remove releases listed as "<release>\t<chart>" lines, keeping their history for helm rollback.
uninstall_releases() {
    local release chart
    while IFS=$'\t' read -r release chart; do
        [[ -n "$release" ]] || continue
        info "Uninstalling $release ($chart); its history stays for helm rollback..."
        helm uninstall "$release" -n "$NS_OSMO_CONTROL_PLANE" --keep-history --wait --timeout "$TIMEOUT_DEPLOY"
    done <<< "$1"
}

wait_for_deployments() {
    local deployment
    while read -r deployment; do
        [[ -n "$deployment" ]] || continue
        kubectl rollout status "deployment/$deployment" -n "$1" --timeout="$TIMEOUT_DEPLOY" >/dev/null || \
            fatal "Deployment $1/$deployment did not become ready"
    done < <(kubectl get deployments -n "$1" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
}

confirm_changes() {
    local answer=""
    printf '\nType the AKS cluster name (%s) to continue: ' "$aks_cluster" >&2
    IFS= read -r answer || true
    if [[ "$answer" != "$aks_cluster" ]]; then
        stage_cancelled=true
        fatal "Not confirmed; nothing was changed"
    fi
    stage_changed=true
}

db_connection() {
    jq -r '.services.postgres as $pg
        | if ($pg.serviceName // "") == "" or ($pg.user // "") == "" then
            error("services.postgres.serviceName and services.postgres.user are required")
          else
            "host=\($pg.serviceName) port=\($pg.port // 5432) dbname=\($pg.db // "osmo") user=\($pg.user) sslmode=\(if ($pg.enabled // false) then "prefer" else "require" end)"
          end' <<< "$1"
}

db_helper_manifest() {
    jq -n --arg name "$1" --arg ns "$NS_OSMO_CONTROL_PLANE" --arg image "$POSTGRES_CLIENT_IMAGE" \
        --arg secret "$SECRET_POSTGRES" '{
            apiVersion: "v1",
            kind: "Pod",
            metadata: {name: $name, namespace: $ns, labels: {"app.kubernetes.io/name": "osmo-upgrade-db"}},
            spec: {
                restartPolicy: "Never",
                terminationGracePeriodSeconds: 1,
                automountServiceAccountToken: false,
                nodeSelector: {"kubernetes.io/os": "linux"},
                containers: [{
                    name: "psql",
                    image: $image,
                    command: ["sleep", "3600"],
                    env: [{name: "PGPASSWORD", valueFrom: {secretKeyRef: {name: $secret, key: "db-password"}}}],
                    resources: {requests: {cpu: "100m", memory: "256Mi"}, limits: {memory: "1Gi"}}
                }]
            }
        }'
}

start_db_helper() {
    helper_pod="osmo-upgrade-db-$(date -u +%H%M%S)"
    db_helper_manifest "$helper_pod" | kubectl apply -f - >/dev/null
    kubectl wait "pod/$helper_pod" -n "$NS_OSMO_CONTROL_PLANE" --for=condition=Ready --timeout=300s >/dev/null || \
        fatal "Database helper pod $helper_pod did not become ready"
}

stop_db_helper() {
    kubectl delete pod "$helper_pod" -n "$NS_OSMO_CONTROL_PLANE" --ignore-not-found --wait=false >/dev/null
    helper_pod=""
}

require_key_vault_secret() {
    az keyvault secret show --vault-name "$1" --name "$2" --query id -o tsv >/dev/null 2>&1 || \
        fatal "Can't read $2 from Key Vault $1. $3"
}

# Build one read-only query from pgroll migration files. It prints a line for each
# object whose state differs from what the migrations need (before) or leave (after).
# pgroll rejects a whole migration when one of these checks fails, and the chart's
# runner logs that as skipped while the Helm hook still succeeds.
MIGRATION_CHECK_JQ=$(cat <<'JQ'
def sq: "'" + gsub("'"; "''") + "'";
[inputs
  | (input_filename | split("/") | last | rtrimstr(".json")) as $m
  | [.operations[] | to_entries[0] | select(.key == "create_table") | .value.name] as $created
  | .operations[] | to_entries[0] | .key as $op | .value as $v
  | if $op == "sql" then empty
    elif $op == "drop_column" then {m: $m, kind: "column", t: $v.table, o: $v.column, e: ($mode == "before")}
    elif $op == "add_column" then
      (if $mode == "before" and ($created | any(. == $v.table) | not)
       then {m: $m, kind: "table", t: $v.table, o: $v.table, e: true} else empty end),
      {m: $m, kind: "column", t: $v.table, o: $v.column.name, e: ($mode == "after")}
    elif $op == "create_table" then {m: $m, kind: "table", t: $v.name, o: $v.name, e: ($mode == "after")}
    elif $op == "create_index" then
      (if $mode == "before" and ($created | any(. == $v.table) | not)
       then {m: $m, kind: "table", t: $v.table, o: $v.table, e: true} else empty end),
      {m: $m, kind: "index", t: $v.table, o: $v.name, e: ($mode == "after")}
    else error("unsupported pgroll operation \($op) in \($m)") end]
| unique
| "WITH checks(migration, kind, table_name, object_name, should_exist) AS (VALUES "
  + (map("(\(.m | sq), \(.kind | sq), \(.t | sq), \(.o | sq), \(.e))") | join(", "))
  + ") SELECT migration || ': ' || kind || ' ' || CASE kind WHEN 'column' THEN table_name || '.' || object_name"
  + " WHEN 'index' THEN object_name || ' on ' || table_name ELSE object_name END"
  + " || CASE WHEN should_exist THEN ' is missing' ELSE ' already exists' END FROM checks c"
  + " WHERE should_exist <> CASE c.kind"
  + " WHEN 'column' THEN EXISTS (SELECT 1 FROM information_schema.columns i WHERE i.table_schema = 'public'"
  + " AND i.table_name = c.table_name AND i.column_name = c.object_name)"
  + " WHEN 'table' THEN EXISTS (SELECT 1 FROM information_schema.tables i WHERE i.table_schema = 'public'"
  + " AND i.table_name = c.object_name)"
  + " ELSE EXISTS (SELECT 1 FROM pg_indexes i WHERE i.schemaname = 'public' AND i.indexname = c.object_name) END"
  + " ORDER BY 1;"
JQ
)

# Usage: migration_check <migrations-dir> <before|after> <connection> > <output-file>
migration_check() {
    local sql
    sql=$(jq -rn --arg mode "$2" "$MIGRATION_CHECK_JQ" "$1"/*.json)
    start_db_helper
    kubectl exec "$helper_pod" -n "$NS_OSMO_CONTROL_PLANE" -- psql "$3" -v ON_ERROR_STOP=1 -tAc "$sql"
    stop_db_helper
}

#------------------------------------------------------------------------------
# Stage State
#------------------------------------------------------------------------------

state_file="$backup_dir/upgrade-state.json"

state_update() {
    local tmp
    tmp=$(mktemp "$backup_dir/.state.XXXXXX")
    jq "$@" "$state_file" > "$tmp"
    mv "$tmp" "$state_file"
}

stage_field() {
    [[ -f "$state_file" ]] || return 0
    jq -r --arg stage "$1" --arg field "$2" '.stages[$stage][$field] // empty' "$state_file"
}

record_stage() {
    # shellcheck disable=SC2016  # jq filter
    state_update --arg stage "$stage" --arg status "$1" --arg now "$(now_utc)" --argjson details "${2:-null}" '
        .stages[$stage] = (
            if $status == "started" then {status: $status, started_at: $now}
            else (.stages[$stage] // {}) + {status: $status, finished_at: $now, details: $details} end)'
}

record_event() {
    # shellcheck disable=SC2016  # jq filter
    state_update --arg stage "$stage" --arg status "$1" --arg now "$(now_utc)" \
        '.events += [{stage: $stage, status: $status, at: $now}]'
}

latest_backup() {
    jq -r '.backups[-1].dir // empty' "$state_file"
}

# The most recently started of export (data-keeping path) and reset (fresh path).
upgrade_path() {
    [[ -f "$state_file" ]] || return 0
    jq -r '[.stages | to_entries[] | select(.key == "export" or .key == "reset")]
        | sort_by(.value.started_at) | last | .key // empty' "$state_file"
}

predecessor_problem() {
    local needed
    case "$stage" in
        backup)        return 0 ;;
        hop-6.2|reset) needed=backup ;;
        tokens)        needed=hop-6.2 ;;
        export)        needed=tokens ;;
        verify)        needed=hop-6.3 ;;
        hop-6.3)
            needed=$(upgrade_path)
            if [[ -z "$needed" ]]; then
                echo "needs a succeeded export (data-keeping path) or reset (fresh path) stage"
                return 0
            fi
            ;;
    esac
    [[ "$(stage_field "$needed" status)" == "succeeded" ]] || echo "needs a succeeded $needed stage"
}

next_step() {
    case "$stage" in
        backup)  echo "hop-6.2 to keep OSMO data, or reset to start fresh" ;;
        hop-6.2) echo "tokens" ;;
        tokens)  echo "export" ;;
        export)  echo "review $bundle_dir/osmo-platforms.yaml, then hop-6.3" ;;
        reset)   echo "generate the bundle's osmo-platforms.yaml from Terraform node_pools, then hop-6.3" ;;
        hop-6.3) echo "verify" ;;
        verify)  echo "none; delete the legacy release history once you no longer need a rollback" ;;
    esac
}

#------------------------------------------------------------------------------
# Stages
#------------------------------------------------------------------------------

stage_backup() {
    local name path service_release operator_release token_secret connection version type code dump table_data
    local ns release chart revision
    name="backup-$(date -u +%Y%m%dT%H%M%SZ)"
    path="$backup_dir/$name"
    mkdir -p "$path/helm" "$path/configs" "$path/k8s"

    section "Save Helm Releases"
    helm list --all -n "$NS_OSMO_CONTROL_PLANE" -o json > "$path/helm/releases-control-plane.json"
    helm list --all -n "$NS_OSMO_OPERATOR" -o json > "$path/helm/releases-operator.json"
    jq -e 'any(.[]; .status != "uninstalled")' "$path/helm/releases-control-plane.json" >/dev/null || \
        fatal "No installed Helm releases in $NS_OSMO_CONTROL_PLANE"
    while IFS=$'\t' read -r ns release chart revision; do
        helm get values "$release" -n "$ns" -o yaml > "$path/helm/$ns.$release.values.yaml"
        helm get values "$release" -n "$ns" -o json > "$path/helm/$ns.$release.values.json"
        helm get manifest "$release" -n "$ns" > "$path/helm/$ns.$release.manifest.yaml"
        info "Saved $ns/$release: $chart, revision $revision"
    done < <(
        jq -r --arg ns "$NS_OSMO_CONTROL_PLANE" '.[] | select(.status != "uninstalled") | [$ns, .name, .chart, .revision] | @tsv' \
            "$path/helm/releases-control-plane.json"
        jq -r --arg ns "$NS_OSMO_OPERATOR" '.[] | select(.status != "uninstalled") | [$ns, .name, .chart, .revision] | @tsv' \
            "$path/helm/releases-operator.json"
    )

    section "Save Kubernetes Objects"
    kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" >/dev/null 2>&1 || \
        fatal "MEK ConfigMap $SECRET_MEK not found in $NS_OSMO_CONTROL_PLANE"
    kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" -o json | \
        jq '{apiVersion, kind, metadata: {name: .metadata.name, namespace: .metadata.namespace}, data}' \
        > "$path/k8s/$SECRET_MEK.json"
    jq -e '(.data // {}) | length > 0' "$path/k8s/$SECRET_MEK.json" >/dev/null || fatal "MEK ConfigMap $SECRET_MEK has no data"
    info "Saved ConfigMap $SECRET_MEK"
    operator_release=$(jq -r '[.[] | select(.status != "uninstalled" and (.chart | startswith("backend-operator-")))][0].name // empty' \
        "$path/helm/releases-operator.json")
    if [[ -n "$operator_release" ]]; then
        token_secret=$(helm get values "$operator_release" -n "$NS_OSMO_OPERATOR" -o json --all | jq -r '.global.accountTokenSecret // empty')
        if [[ -n "$token_secret" ]] && kubectl get secret "$token_secret" -n "$NS_OSMO_OPERATOR" >/dev/null 2>&1; then
            kubectl get secret "$token_secret" -n "$NS_OSMO_OPERATOR" -o json | \
                jq '{apiVersion, kind, type, metadata: {name: .metadata.name, namespace: .metadata.namespace}, data}' \
                > "$path/k8s/$token_secret.json"
            info "Saved Secret $NS_OSMO_OPERATOR/$token_secret"
        fi
    fi

    section "Save OSMO Configs"
    require_service_url
    version=$(service_version)
    info "OSMO $version at $service_url"
    for type in "${CONFIG_TYPES[@]}"; do
        code=$(fetch_config "$type" "$path/configs/$type.json")
        case "$code" in
            200) info "Saved $type config" ;;
            404)
                rm -f "$path/configs/$type.json"
                if is_required_config "$type"; then
                    fatal "OSMO $version has no $type config endpoint"
                fi
                warn "OSMO $version has no $type config endpoint; skipped"
                ;;
            *) fatal "Reading the $type config failed (HTTP $code)" ;;
        esac
    done

    section "Dump OSMO Database"
    service_release=$(jq -r '[.[] | select(.status != "uninstalled" and (.chart | startswith("service-")))][0].name // empty' \
        "$path/helm/releases-control-plane.json")
    [[ -n "$service_release" ]] || fatal "No OSMO service release in $NS_OSMO_CONTROL_PLANE"
    connection=$(db_connection "$(helm get values "$service_release" -n "$NS_OSMO_CONTROL_PLANE" -o json --all)")
    printf '%s\n' "$connection" > "$path/db-connection.txt"
    db_helper_manifest osmo-upgrade-db-restore > "$path/db-helper-pod.json"
    start_db_helper
    dump="$path/osmo-db.dump"
    kubectl exec "$helper_pod" -n "$NS_OSMO_CONTROL_PLANE" -- pg_dump --format=custom --dbname "$connection" > "$dump"
    [[ -s "$dump" ]] || fatal "The database dump is empty"
    table_data=$(kubectl exec -i "$helper_pod" -n "$NS_OSMO_CONTROL_PLANE" -- pg_restore --list < "$dump" | \
        grep -c ' TABLE DATA ' || true)
    (( table_data > 0 )) || fatal "The database dump has no table data"
    stop_db_helper
    info "Dumped the database: $(wc -c < "$dump" | tr -d ' ') bytes, $table_data table data entries"

    (cd "$path" && find . -type f ! -name SHA256SUMS | LC_ALL=C sort | while read -r file; do
        printf '%s  %s\n' "$(calculate_sha256 "$file")" "$file"
    done) > "$path/SHA256SUMS"

    stage_details=$(jq -n --arg dir "$name" --arg now "$(now_utc)" --arg version "$version" --arg release "$service_release" \
        --arg connection "$connection" --argjson bytes "$(wc -c < "$dump" | tr -d ' ')" --argjson tables "$table_data" \
        '{dir: $dir, created_at: $now, service_version: $version, service_release: $release, db_connection: $connection,
          dump_bytes: $bytes, table_data_entries: $tables}')
    # shellcheck disable=SC2016  # jq filter
    state_update --argjson backup "$stage_details" '.backups += [$backup]'
    info "Backup saved to $path"
}

stage_hop_62() {
    local backup_path releases target_line release chart_name chart_version line saved tgz timeout version
    local plan="" upgrades="" service_tgz="" service_release="" migrations_dir="" connection=""
    local -a args
    backup_path="$backup_dir/$(latest_backup)"
    releases=$(helm list --all -n "$NS_OSMO_CONTROL_PLANE" -o json)
    if jq -e 'any(.[]; .name == "osmo" and .status != "uninstalled")' <<< "$releases" >/dev/null; then
        fatal "Release osmo exists in $NS_OSMO_CONTROL_PLANE; this install already uses the 6.3 layout"
    fi
    # Service first: its pre-upgrade hook runs the database migrations.
    plan=$(jq -r '.[] | select(.status != "uninstalled")
        | (.chart | capture("^(?<name>.+)-(?<version>[0-9]+\\.[0-9]+\\.[0-9]+.*)$")) as $c
        | select($c.name | IN("service", "router", "web-ui"))
        | [(if $c.name == "service" then 0 else 1 end), .name, $c.name, $c.version] | @tsv' <<< "$releases" | \
        sort -n | cut -f2-)
    awk -F'\t' '$2 == "service" {found = 1} END {exit !found}' <<< "$plan" || \
        fatal "No legacy service release in $NS_OSMO_CONTROL_PLANE"
    target_line=$(chart_line "$OSMO_UPGRADE_62_CHART_VERSION")

    section "Preflight"
    helm repo add osmo "$HELM_REPO_OSMO" >/dev/null 2>&1 || true
    helm repo update osmo >/dev/null
    while IFS=$'\t' read -r release chart_name chart_version; do
        line=$(chart_line "$chart_version")
        if [[ "$line" == "$target_line" ]]; then
            info "$release already runs $chart_name $chart_version; skipping"
            continue
        fi
        line_older_than "$line" "$target_line" || fatal "$release runs $chart_name $chart_version, newer than $OSMO_UPGRADE_62_CHART_VERSION"
        saved="$backup_path/helm/$NS_OSMO_CONTROL_PLANE.$release.values.json"
        [[ -f "$saved" ]] || fatal "The latest backup has no values for $release; run --stage backup again"
        if ! diff <(jq -S . "$saved") <(helm get values "$release" -n "$NS_OSMO_CONTROL_PLANE" -o json | jq -S .) >/dev/null; then
            fatal "Helm values for $release changed after the latest backup; run --stage backup again"
        fi
        if jq -e '(.sidecars.envoy.enabled == true) or (.sidecars.envoy.oauth2Filter.enabled == true) or (.services.service.auth.enabled == true)' \
            "$saved" >/dev/null; then
            fatal "$release enables OSMO authentication. Follow NVIDIA's 6.0 to 6.2 authentication steps; this script upgrades no-auth installs."
        fi
        case "$chart_name" in
            service)
                tgz=$(pull_and_verify_chart "osmo/service" "$OSMO_UPGRADE_62_CHART_VERSION" "$OSMO_UPGRADE_62_SERVICE_CHART_SHA256" "$work_dir/charts/service")
                service_tgz="$tgz"
                service_release="$release"
                ;;
            router)  tgz=$(pull_and_verify_chart "osmo/router" "$OSMO_UPGRADE_62_CHART_VERSION" "$OSMO_UPGRADE_62_ROUTER_CHART_SHA256" "$work_dir/charts/router") ;;
            web-ui)  tgz=$(pull_and_verify_chart "osmo/web-ui" "$OSMO_UPGRADE_62_CHART_VERSION" "$OSMO_UPGRADE_62_WEB_UI_CHART_SHA256" "$work_dir/charts/web-ui") ;;
        esac
        upgrades+="$release"$'\t'"$chart_name"$'\t'"$chart_version"$'\t'"$tgz"$'\n'
    done <<< "$plan"
    [[ -n "$upgrades" ]] || fatal "No legacy releases need the 6.2 upgrade"

    if [[ -n "$service_tgz" ]]; then
        section "Check Database Schema"
        migrations_dir="$work_dir/migrations"
        mkdir -p "$migrations_dir"
        tar -xzf "$service_tgz" -C "$migrations_dir" --strip-components=2 service/migrations
        compgen -G "$migrations_dir/*.json" >/dev/null || fatal "Chart $OSMO_UPGRADE_62_CHART_VERSION has no service/migrations files"
        connection=$(db_connection "$(helm get values "$service_release" -n "$NS_OSMO_CONTROL_PLANE" -o json --all)")
        migration_check "$migrations_dir" before "$connection" > "$work_dir/migration-check.txt"
        if [[ -s "$work_dir/migration-check.txt" ]]; then
            while read -r line; do error "$line"; done < "$work_dir/migration-check.txt"
            fatal "The database doesn't match the schema NVIDIA's 6.2 migrations expect, so pgroll would skip them and leave OSMO without its 6.2 schema. Pre-release builds can differ from the 6.0 release. Use the fresh path (--stage reset) instead."
        fi
        info "The database matches the schema the 6.2 migrations expect"
    fi

    section "Plan: Upgrade to OSMO $OSMO_UPGRADE_62_IMAGE_VERSION"
    while IFS=$'\t' read -r release chart_name chart_version tgz; do
        if [[ -n "$release" ]]; then
            echo "  $release: $chart_name $chart_version -> $OSMO_UPGRADE_62_CHART_VERSION"
        fi
    done <<< "$upgrades"
    echo "  Images: nvcr.io/nvidia/osmo/*:$OSMO_UPGRADE_62_IMAGE_VERSION"
    echo "  The service release's pre-upgrade hook migrates the database (pgroll)."
    echo "  Sidecars stay off (envoy, oauth2Proxy, rateLimit, authz), matching a no-auth install."
    echo "  Rollback: the runbook restores $backup_path"
    confirm_changes

    section "Upgrade Releases"
    while IFS=$'\t' read -r release chart_name chart_version tgz; do
        [[ -n "$release" ]] || continue
        timeout="$TIMEOUT_DEPLOY"
        args=(upgrade "$release" "$tgz" -n "$NS_OSMO_CONTROL_PLANE"
            -f "$backup_path/helm/$NS_OSMO_CONTROL_PLANE.$release.values.json"
            --set-string "global.osmoImageLocation=nvcr.io/nvidia/osmo"
            --set-string "global.osmoImageTag=$OSMO_UPGRADE_62_IMAGE_VERSION"
            --set sidecars.envoy.enabled=false
            --set sidecars.oauth2Proxy.enabled=false)
        case "$chart_name" in
            service)
                timeout=1200s
                args+=(--set services.migration.enabled=true --set sidecars.rateLimit.enabled=false --set sidecars.authz.enabled=false)
                ;;
            router) args+=(--set sidecars.authz.enabled=false) ;;
        esac
        info "Upgrading $release to $chart_name $OSMO_UPGRADE_62_CHART_VERSION..."
        helm "${args[@]}" --wait --timeout "$timeout"
    done <<< "$upgrades"
    wait_for_deployments "$NS_OSMO_CONTROL_PLANE"

    if [[ -n "$service_tgz" ]]; then
        migration_check "$migrations_dir" after "$connection" > "$work_dir/migration-check.txt"
        if [[ -s "$work_dir/migration-check.txt" ]]; then
            while read -r line; do error "$line"; done < "$work_dir/migration-check.txt"
            fatal "The 6.2 database migrations didn't fully apply. Restore the backup with the rollback runbook, or use the fresh path (--stage reset)."
        fi
        info "The 6.2 database migrations applied"
    fi

    require_service_url
    version=$(service_version)
    [[ "$version" == 6.2.* ]] || fatal "OSMO reports $version after the 6.2 upgrade"
    info "OSMO $version is running with migrated data"
    stage_details=$(jq -n --arg version "$version" --argjson releases "$(release_names_json "$upgrades")" \
        '{service_version: $version, releases: $releases}')
}

stage_tokens() {
    local version operator_release token_secret token_name token others
    require_tools osmo
    osmo_cli user --help >/dev/null 2>&1 || \
        fatal "This osmo CLI has no 'osmo user' command. Install OSMO CLI 6.2.10 or $OSMO_IMAGE_VERSION first."
    require_service_url
    version=$(service_version)
    [[ "$version" == 6.2.* ]] || fatal "The tokens stage expects OSMO 6.2 (found $version)"
    operator_release=$(helm list -n "$NS_OSMO_OPERATOR" -o json | \
        jq -r '[.[] | select(.chart | startswith("backend-operator-"))][0].name // empty')
    [[ -n "$operator_release" ]] || fatal "No backend-operator release in $NS_OSMO_OPERATOR"
    token_secret=$(helm get values "$operator_release" -n "$NS_OSMO_OPERATOR" -o json --all | jq -r '.global.accountTokenSecret // empty')
    [[ -n "$token_secret" ]] || fatal "$operator_release doesn't set global.accountTokenSecret"

    section "Create Backend Operator Token"
    osmo_cli login "$service_url/" --method dev --username "$OSMO_API_USER" >/dev/null || fatal "osmo login failed for $service_url"
    stage_changed=true
    if osmo_cli user get backend-operator >/dev/null 2>&1; then
        info "User backend-operator exists"
    else
        osmo_cli user create backend-operator --roles osmo-backend >/dev/null
        info "Created user backend-operator with role osmo-backend"
    fi
    token_name="backend-operator-$(date -u +%Y%m%dT%H%M%SZ)"
    token=$(osmo_cli token set "$token_name" --user backend-operator --expires-at "$token_expiry" \
        --description "Backend operator token (upgrade-osmo.sh)" --roles osmo-backend -t json | jq -r '.token // empty')
    [[ -n "$token" ]] || fatal "osmo token set returned no token"
    kubectl create secret generic "$token_secret" -n "$NS_OSMO_OPERATOR" --from-file=token=<(printf '%s' "$token") \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    token=""
    info "Stored token $token_name (expires $token_expiry) in $NS_OSMO_OPERATOR/$token_secret"

    section "Restart Backend Operator"
    kubectl rollout restart deployment -n "$NS_OSMO_OPERATOR" >/dev/null
    wait_for_deployments "$NS_OSMO_OPERATOR"
    wait_backend_online "$backend_name" 600 || fatal "Backend $backend_name isn't online 10 minutes after the token update"
    info "Backend $backend_name is online"
    others=$(jq -r --arg name "$backend_name" '[.backends[]? | select(.name != $name) | .name] | join(", ")' "$work_dir/backends.json")
    if [[ -n "$others" ]]; then
        warn "Renew these HiL backend tokens with 04-prepare-osmo-hil-node.sh --renew-token: $others"
    else
        info "No other backends need a token"
    fi
    stage_details=$(jq -n --arg token "$token_name" --arg expiry "$token_expiry" --arg secret "$NS_OSMO_OPERATOR/$token_secret" \
        --arg others "$others" '{token_name: $token, expires_at: $expiry, secret: $secret, hil_backends: ($others | split(", ") | map(select(length > 0)))}')
}

stage_export() {
    local name path exporter backup_configs expected actual missing bundle_values line
    require_service_url
    name="export-$(date -u +%Y%m%dT%H%M%SZ)"
    path="$backup_dir/$name"
    mkdir -p "$path"

    section "Export Configs"
    exporter="$work_dir/export_configs_to_helm.py"
    curl -fsSL --retry 2 -o "$exporter" "$EXPORTER_URL" || fatal "Could not download $EXPORTER_URL"
    [[ "$(calculate_sha256 "$exporter")" == "$OSMO_UPGRADE_EXPORTER_SHA256" ]] || \
        fatal "export_configs_to_helm.py doesn't match OSMO_UPGRADE_EXPORTER_SHA256"
    info "Verified NVIDIA/OSMO@${OSMO_UPGRADE_TOOLS_COMMIT:0:12} export_configs_to_helm.py"
    if ! run_python "$exporter" --url "$service_url" --header "x-osmo-user: $OSMO_API_USER" \
        > "$path/osmo-configs.yaml" 2> "$path/export.log"; then
        cat "$path/export.log" >&2
        fatal "The config export failed"
    fi
    if grep -q '^Error connecting' "$path/export.log"; then
        cat "$path/export.log" >&2
        fatal "The exporter couldn't reach $service_url"
    fi
    while read -r line; do
        warn "$line"
    done < <(grep '^Error fetching' "$path/export.log" || true)
    yaml_to_json "$path/osmo-configs.yaml" > "$path/osmo-configs.json"

    section "Compare With Backup"
    backup_configs="$backup_dir/$(latest_backup)/configs"
    expected=$(api_object_names "$backup_configs")
    actual=$(values_object_names "$path/osmo-configs.json")
    missing=$(missing_objects "$expected" "$actual")
    jq -rn --argjson names "$actual" '$names | to_entries[] | "  \(.key): \(.value | length)"'
    if [[ -n "$missing" ]]; then
        printf '%s\n' "$missing" > "$path/missing-objects.txt"
        while read -r line; do error "Not exported: $line"; done <<< "$missing"
        fatal "The export is missing objects from the backup"
    fi
    info "Every backed-up pool, platform, pod template, and backend is in the export"

    section "Write Bundle Values"
    bundle_values="$bundle_dir/osmo-platforms.yaml"
    if [[ ! -e "$bundle_values" ]]; then
        cp "$path/osmo-configs.yaml" "$bundle_values"
        info "Wrote $bundle_values"
    elif cmp -s "$path/osmo-configs.yaml" "$bundle_values"; then
        info "$bundle_values already matches this export"
    else
        warn "$bundle_values exists and differs from this export, so it was left unchanged."
        warn "Reconcile it with $path/osmo-configs.yaml before hop-6.3."
    fi
    while read -r line; do
        warn "$line. Create each as a Kubernetes Secret in $NS_OSMO_CONTROL_PLANE, or remove the settings that use it."
    done < <(grep -E '^Found [0-9]+ secret references' "$path/export.log" || true)
    stage_details=$(jq -n --arg dir "$name" --arg bundle "$bundle_values" '{dir: $dir, bundle_values: $bundle}')
}

stage_reset() {
    local legacy key_vault pg_fqdn redis_hostname db_name backup_values backup_host secret
    legacy=$(osmo_legacy_releases "$NS_OSMO_CONTROL_PLANE" "$OSMO_CHART_VERSION")
    pg_fqdn=$(tf_get "$tf_output" "postgresql_connection_info.value.fqdn" "")
    redis_hostname=$(tf_get "$tf_output" "managed_redis_connection_info.value.hostname" "")
    key_vault=$(tf_get "$tf_output" "key_vault_name.value" "")
    [[ -n "$pg_fqdn" && -n "$redis_hostname" && -n "$key_vault" ]] || \
        fatal "reset needs the external PostgreSQL, Managed Redis, and Key Vault Terraform outputs"
    backup_values="$backup_dir/$(latest_backup)/helm/$NS_OSMO_CONTROL_PLANE.$(jq -r '.backups[-1].service_release' "$state_file").values.json"
    db_name=$(jq -r '.services.postgres.db // "osmo"' "$backup_values" 2>/dev/null || echo osmo)
    backup_host=$(jq -r '.backups[-1].db_connection // ""' "$state_file" | sed -n 's/^host=\([^ ]*\).*/\1/p')

    section "Preflight"
    [[ "$backup_host" == "$pg_fqdn" ]] || \
        fatal "The backed-up database host (${backup_host:-unknown}) isn't the Terraform PostgreSQL server ($pg_fqdn)"
    for secret in psql-admin-password redis-primary-key; do
        require_key_vault_secret "$key_vault" "$secret" "cleanup/uninstall-osmo.sh needs it."
    done
    kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" >/dev/null 2>&1 || \
        fatal "MEK ConfigMap $SECRET_MEK not found in $NS_OSMO_CONTROL_PLANE"

    section "Plan: Reset OSMO Data"
    echo "  Uninstall (history kept for helm rollback): $(describe_releases "$legacy")"
    echo "  Purge: PostgreSQL database $db_name (public schema) on $pg_fqdn, and Redis {osmo}:* keys"
    echo "  Keep: storage container, $SECRET_MEK, secrets, namespaces, and the backend operator"
    echo "  OSMO workflow records, dataset records, and config history in the database are deleted."
    confirm_changes

    section "Reset"
    uninstall_releases "$legacy"
    "$SETUP_DIR/cleanup/uninstall-osmo.sh" -t "$tf_dir" "${target_args[@]}" --skip-backend --skip-k8s-cleanup \
        --purge-postgres --purge-redis --db-name "$db_name"
    kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" >/dev/null 2>&1 || \
        fatal "MEK ConfigMap $SECRET_MEK is missing after the reset; restore it from the backup"
    stage_details=$(jq -n --argjson removed "$(release_names_json "$legacy")" --arg db "$db_name" \
        '{removed_releases: $removed, purged_database: $db}')
}

stage_hop_63() {
    local path values_json names legacy mek_owner missing values_sha line key_vault
    local -a deploy_command
    path=$(upgrade_path)
    values_json="$work_dir/platform-values.json"

    section "Check Platform Values"
    yaml_to_json "$platform_values" > "$values_json" || fatal "Can't parse $platform_values"
    jq -e '.services.configs | type == "object"' "$values_json" >/dev/null || \
        fatal "$platform_values has no services.configs mapping"
    names=$(values_object_names "$values_json")
    jq -rn --argjson names "$names" '$names | to_entries[] | "  \(.key): \(.value | join(", "))"'
    if [[ "$path" == "export" ]]; then
        missing=$(missing_objects "$(api_object_names "$backup_dir/$(latest_backup)/configs")" "$names")
        if [[ -n "$missing" ]]; then
            while read -r line; do
                warn "Not carried over from the backup: $line"
            done <<< "$missing"
        fi
    fi
    deploy_command=("$SETUP_DIR/03-deploy-osmo.sh" -t "$tf_dir" "${target_args[@]}" --platform-values "$platform_values"
        ${deploy_args[@]+"${deploy_args[@]}"})
    "${deploy_command[@]}" --config-preview >/dev/null || fatal "03-deploy-osmo.sh rejected the deployment options"

    section "Preflight"
    legacy=$(osmo_legacy_releases "$NS_OSMO_CONTROL_PLANE" "$OSMO_CHART_VERSION")
    key_vault=$(tf_get "$tf_output" "key_vault_name.value" "")
    [[ -n "$key_vault" ]] || fatal "hop-6.3 needs the Key Vault Terraform output"
    require_key_vault_secret "$key_vault" osmo-admin-password \
        "03-deploy-osmo.sh mounts it through its SecretProviderClass. Set osmo_config.should_create_secret = true in terraform.tfvars and apply Terraform first."
    if [[ " ${deploy_args[*]:-} " != *" --use-incluster-postgres "* ]]; then
        require_key_vault_secret "$key_vault" psql-admin-password "03-deploy-osmo.sh needs it for external PostgreSQL."
    fi
    if [[ " ${deploy_args[*]:-} " != *" --use-incluster-redis "* ]]; then
        require_key_vault_secret "$key_vault" redis-primary-key "03-deploy-osmo.sh needs it for Managed Redis."
    fi
    kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" >/dev/null 2>&1 || \
        fatal "MEK ConfigMap $SECRET_MEK not found in $NS_OSMO_CONTROL_PLANE"
    mek_owner=$(kubectl get configmap "$SECRET_MEK" -n "$NS_OSMO_CONTROL_PLANE" \
        -o jsonpath='{.metadata.annotations.meta\.helm\.sh/release-name}')
    if [[ -n "$mek_owner" ]] && cut -f1 <<< "$legacy" | grep -qx "$mek_owner"; then
        fatal "Release $mek_owner owns $SECRET_MEK, so uninstalling it would delete the MEK. Annotate the ConfigMap with helm.sh/resource-policy=keep first."
    fi
    if [[ "$path" == "export" && ! "$(jq -r '.backups[-1].created_at // ""' "$state_file")" > "$(stage_field hop-6.2 finished_at)" ]]; then
        warn "No backup since hop-6.2. Run --stage backup first if you want a 6.2 restore point."
    fi

    section "Plan: Install OSMO $OSMO_IMAGE_VERSION"
    echo "  Path: $([[ "$path" == "export" ]] && echo "data-keeping" || echo "fresh")"
    echo "  Uninstall (history kept for helm rollback): $(describe_releases "$legacy")"
    echo "  Keep: $SECRET_MEK, database and Redis secrets, the external database, and storage"
    echo "  Then run: ${deploy_command[*]}"
    echo "  The control plane is down for several minutes while releases are replaced."
    confirm_changes

    section "Install OSMO $OSMO_IMAGE_VERSION"
    uninstall_releases "$legacy"
    "${deploy_command[@]}"
    helm list -n "$NS_OSMO_CONTROL_PLANE" -o json | \
        jq -e --arg chart "$OSMO_SERVICE_CHART-$OSMO_CHART_VERSION" 'any(.[]; .name == "osmo" and .chart == $chart and .status == "deployed")' >/dev/null || \
        fatal "Release osmo isn't deployed on $OSMO_SERVICE_CHART-$OSMO_CHART_VERSION"
    if [[ " ${deploy_args[*]:-} " != *" --skip-backend "* ]]; then
        helm list -n "$NS_OSMO_OPERATOR" -o json | \
            jq -e --arg chart "$OSMO_BACKEND_CHART-$OSMO_CHART_VERSION" 'any(.[]; .name == "osmo-operator" and .chart == $chart and .status == "deployed")' >/dev/null || \
            fatal "Release osmo-operator isn't deployed on $OSMO_BACKEND_CHART-$OSMO_CHART_VERSION"
    fi
    values_sha=$(calculate_sha256 "$platform_values")
    stage_details=$(jq -n --arg path "$path" --arg values "$platform_values" --arg sha "$values_sha" \
        --argjson removed "$(release_names_json "$legacy")" \
        '{path: $path, platform_values: $values, platform_values_sha256: $sha, removed_releases: $removed}')
}

stage_verify() {
    local version values_file values_json live_dir type code missing client_version line
    local -a problems=()
    require_service_url
    values_file=$(jq -r '.stages["hop-6.3"].details.platform_values // empty' "$state_file")
    [[ -f "$values_file" ]] || fatal "Platform values from hop-6.3 not found: $values_file"
    [[ "$(calculate_sha256 "$values_file")" == "$(jq -r '.stages["hop-6.3"].details.platform_values_sha256' "$state_file")" ]] || \
        warn "$values_file changed after hop-6.3; checking the current file"

    section "Version"
    version=$(service_version)
    if [[ "$version" == "$OSMO_IMAGE_VERSION" ]]; then
        info "OSMO $version matches the pin"
    else
        problems+=("OSMO reports $version, expected $OSMO_IMAGE_VERSION")
    fi
    if command -v osmo >/dev/null 2>&1 && osmo_cli login "$service_url/" --method dev --username "$OSMO_API_USER" >/dev/null 2>&1; then
        osmo_cli version || true
        client_version=$(osmo_cli version 2>/dev/null | awk -F': *' '/client version/ {print $2}')
        [[ "$client_version" == "${OSMO_IMAGE_VERSION%.*}".* ]] || \
            warn "Local OSMO CLI is ${client_version:-unknown}; upgrade it to $OSMO_IMAGE_VERSION"
    fi

    section "Configured Objects"
    values_json="$work_dir/platform-values.json"
    yaml_to_json "$values_file" > "$values_json"
    live_dir="$work_dir/live"
    mkdir -p "$live_dir"
    for type in pool pod_template backend; do
        code=$(fetch_config "$type" "$live_dir/$type.json")
        [[ "$code" == "200" ]] || fatal "Reading the $type config failed (HTTP $code)"
    done
    missing=$(missing_objects "$(values_object_names "$values_json")" "$(api_object_names "$live_dir")")
    if [[ -n "$missing" ]]; then
        while read -r line; do problems+=("Not configured: $line"); done <<< "$missing"
    else
        info "Every pool, platform, pod template, and backend in $values_file is configured"
    fi

    section "Backends"
    if wait_backend_online "$backend_name" 300; then
        info "Backend $backend_name is online"
    else
        problems+=("Backend $backend_name isn't online")
    fi
    jq -r '.backends[]? | "  \(.name): \(if .online then "online" else "offline" end)"' "$work_dir/backends.json" 2>/dev/null || true

    section "ConfigMap Mode"
    code=$(api_request PATCH api/configs/service "$work_dir/patch.json" \
        '{"configs_dict": {}, "description": "upgrade-osmo.sh ConfigMap mode check"}')
    if [[ "$code" == "409" ]]; then
        info "Config writes return 409, so configs come from Helm values"
    else
        problems+=("A config write returned HTTP $code, expected 409 in ConfigMap mode")
    fi

    if (( ${#problems[@]} > 0 )); then
        for line in "${problems[@]}"; do error "$line"; done
        fatal "Verification failed"
    fi
    stage_details=$(jq -n --arg version "$version" '{service_version: $version}')
}

#------------------------------------------------------------------------------
# Validate Inputs
#------------------------------------------------------------------------------

[[ -n "$stage" ]] || fatal "--stage is required (${STAGES[*]})"
stage_known=false
for known_stage in "${STAGES[@]}"; do
    [[ "$known_stage" == "$stage" ]] && stage_known=true
done
[[ "$stage_known" == "true" ]] || fatal "Unknown stage '$stage' (expected one of: ${STAGES[*]})"
[[ -n "$backup_dir" ]] || fatal "--backup-dir is required"
require_external_runtime_path "$backup_dir"
[[ -z "$bundle_dir" ]] || bundle_dir=$(absolute_path "$bundle_dir")
[[ -n "$platform_values" || -z "$bundle_dir" ]] || platform_values="$bundle_dir/osmo-platforms.yaml"
[[ -z "$platform_values" ]] || platform_values=$(absolute_path "$platform_values")

if (( ${#deploy_args[@]} > 0 )); then
    [[ "$stage" == "hop-6.3" ]] || fatal "Options after -- apply only to --stage hop-6.3"
    for arg in "${deploy_args[@]}"; do
        case "$arg" in
            --force-mek|--mek-config-file|--mek-config-file=*)
                fatal "hop-6.3 keeps the existing MEK; remove $arg" ;;
            -t|--tf-dir|--tf-dir=*|--kubeconfig|--kubeconfig=*|--context|--context=*|--expected-aks-resource-id|--expected-aks-resource-id=*|--platform-values|--platform-values=*|--config-preview)
                fatal "Pass $arg to $(basename "$0") instead of after --" ;;
        esac
    done
fi

case "$stage" in
    tokens)
        [[ "$token_expiry" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || fatal "--token-expiry YYYY-MM-DD is required for the tokens stage"
        jq -n -e --arg expiry "${token_expiry}T23:59:59Z" '$expiry | fromdateiso8601 > now' >/dev/null || \
            fatal "--token-expiry must be in the future"
        ;;
    export)
        [[ -n "$bundle_dir" ]] || fatal "--bundle-dir is required for the export stage"
        ;;
    hop-6.3)
        [[ -n "$platform_values" ]] || fatal "--platform-values or --bundle-dir is required for hop-6.3"
        ;;
esac

#------------------------------------------------------------------------------
# Resolve Target
#------------------------------------------------------------------------------

require_tools az terraform kubectl helm jq curl

section "Read Terraform Outputs"
tf_output=$(read_terraform_outputs "$tf_dir")
if [[ "$kubeconfig_set" == "true" && -z "$expected_aks_resource_id" ]]; then
    fatal "--expected-aks-resource-id is required with --kubeconfig"
fi
expected_aks_resource_id="${expected_aks_resource_id:-$(tf_require "$tf_output" "aks_cluster.value.id" "AKS cluster resource ID")}"
verify_aks_resource_id "$tf_output" "$expected_aks_resource_id"
resource_group=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
aks_cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster")
kubeconfig="${kubeconfig:-$HOME/.kube/physical-ai-toolchain/${aks_cluster}.yaml}"
context="${context:-$aks_cluster}"
target_args=(--kubeconfig "$kubeconfig" --context "$context" --expected-aks-resource-id "$expected_aks_resource_id")

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
    section "Configuration Preview"
    print_kv "Stage" "$stage"
    print_kv "Backup Dir" "$backup_dir"
    if [[ -f "$state_file" ]]; then
        print_kv "Stage State" "$(jq -r '[.stages | to_entries[] | "\(.key)=\(.value.status)"] | join(" ") | if . == "" then "none" else . end' "$state_file")"
    else
        print_kv "Stage State" "none"
    fi
    readiness=$(predecessor_problem)
    print_kv "Ready" "${readiness:-yes}"
    print_kv "Cluster" "$aks_cluster"
    print_kv "Resource Group" "$resource_group"
    print_kv "AKS Resource ID" "$expected_aks_resource_id"
    print_kv "Kubeconfig" "$kubeconfig"
    print_kv "Context" "$context"
    print_kv "Namespace" "$NS_OSMO_CONTROL_PLANE"
    print_kv "Service URL" "${service_url:-auto-detect}"
    case "$stage" in
        backup)
            print_kv "Saves" "database dump, $SECRET_MEK, Helm values, operator token, configs"
            ;;
        hop-6.2)
            print_kv "Chart" "$OSMO_UPGRADE_62_CHART_VERSION (service, router, web-ui)"
            print_kv "Images" "nvcr.io/nvidia/osmo/*:$OSMO_UPGRADE_62_IMAGE_VERSION"
            ;;
        tokens)
            print_kv "Token Expiry" "${token_expiry:-<required>}"
            print_kv "Backend" "$backend_name"
            ;;
        export)
            print_kv "Exporter" "NVIDIA/OSMO@${OSMO_UPGRADE_TOOLS_COMMIT:0:12}"
            print_kv "Bundle Dir" "$bundle_dir"
            ;;
        reset)
            print_kv "Purges" "PostgreSQL public schema, Redis {osmo}:* keys"
            print_kv "Keeps" "storage container, $SECRET_MEK, secrets, backend operator"
            ;;
        hop-6.3)
            hop_path=$(upgrade_path)
            case "$hop_path" in
                export) print_kv "Path" "data-keeping (export)" ;;
                reset)  print_kv "Path" "fresh (reset)" ;;
                *)      print_kv "Path" "<run export or reset first>" ;;
            esac
            print_kv "Platform Values" "$platform_values"
            print_kv "Chart" "$OSMO_CHART_VERSION"
            print_kv "Image" "$OSMO_IMAGE_VERSION"
            print_kv "03 Options" "${deploy_args[*]:-none}"
            ;;
        verify)
            print_kv "Expected Version" "$OSMO_IMAGE_VERSION"
            print_kv "Backend" "$backend_name"
            ;;
    esac
    exit 0
fi

#------------------------------------------------------------------------------
# Prepare Stage
#------------------------------------------------------------------------------

case "$stage" in
    tokens) require_tools osmo ;;
    export|hop-6.3|verify) require_tools uv ;;
esac
case "$stage" in
    export) [[ -d "$bundle_dir" ]] || fatal "Bundle directory not found: $bundle_dir" ;;
    hop-6.3) [[ -f "$platform_values" && ! -L "$platform_values" ]] || fatal "Platform values must be a regular file: $platform_values" ;;
esac

if [[ ! -e "$backup_dir" ]]; then
    mkdir -p "$backup_dir"
    chmod 700 "$backup_dir"
fi
require_protected_directory "$backup_dir"
if [[ ! -f "$state_file" ]]; then
    jq -n --arg id "$expected_aks_resource_id" --arg ns "$NS_OSMO_CONTROL_PLANE" \
        '{version: 1, aks_resource_id: $id, namespace: $ns, backups: [], stages: {}, events: []}' > "$state_file"
fi
require_protected_file "$state_file"
jq -e --arg id "$expected_aks_resource_id" '(.aks_resource_id | ascii_downcase) == ($id | ascii_downcase)' "$state_file" >/dev/null || \
    fatal "$state_file belongs to a different AKS cluster; use one --backup-dir per cluster"
readiness=$(predecessor_problem)
[[ -z "$readiness" ]] || fatal "Stage $stage $readiness (see $state_file)"

work_dir=$(mktemp -d "$backup_dir/.work.XXXXXX")
helper_pod=""
stage_running=false
stage_changed=false
stage_cancelled=false
stage_details="null"

on_exit() {
    local rc=$?
    set +o errexit
    if [[ -n "$helper_pod" ]]; then
        kubectl delete pod "$helper_pod" -n "$NS_OSMO_CONTROL_PLANE" --ignore-not-found --wait=false >/dev/null 2>&1
    fi
    rm -rf "$work_dir"
    if [[ "$stage_running" == "true" && "$rc" -ne 0 ]]; then
        if [[ "$stage_changed" == "true" ]]; then
            record_stage failed "{\"exit_code\": $rc}"
            error "Stage $stage failed after making changes. Fix the cause and rerun it, or follow the rollback runbook in docs/infrastructure/osmo-upgrade.md."
        else
            printf '%s\n' "$state_snapshot" > "$state_file"
            record_event "$([[ "$stage_cancelled" == "true" ]] && echo cancelled || echo stopped-before-changes)"
            info "Stage $stage stopped before making changes"
        fi
    fi
    exit "$rc"
}
trap on_exit EXIT

section "Connect to Cluster"
verify_existing_aks_kubeconfig "$kubeconfig" "$context" "$expected_aks_resource_id"
connect_aks "$resource_group" "$aks_cluster" "$kubeconfig" "$context"

#------------------------------------------------------------------------------
# Run Stage
#------------------------------------------------------------------------------

state_snapshot=$(cat "$state_file")
record_stage started
stage_running=true
case "$stage" in
    backup)  stage_backup ;;
    hop-6.2) stage_hop_62 ;;
    tokens)  stage_tokens ;;
    export)  stage_export ;;
    reset)   stage_reset ;;
    hop-6.3) stage_hop_63 ;;
    verify)  stage_verify ;;
esac
record_stage succeeded "$stage_details"
stage_running=false

section "Stage Complete"
print_kv "Stage" "$stage"
print_kv "State" "$state_file"
print_kv "Next" "$(next_step)"
