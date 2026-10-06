#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Launch detached Physical AI cloud E2E test attempts and expose a Copilot tracking handle.
# cspell:ignore amlcompute azureml chdir finalizers finetune junitxml keepalive microsoftonline nodepool nohup pids pytest toplevel worktree
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$SCRIPT_DIR/run-e2e-tests.sh"
SKILL_REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null ||
    (cd "$SCRIPT_DIR/../../../.." && pwd))"
# shellcheck source=../../../../scripts/lib/common.sh
source "$SKILL_REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=lib/handle-state.sh
source "$SCRIPT_DIR/lib/handle-state.sh"
DEFAULT_TESTS=()
KNOWN_TESTS=()
TESTS=()

discover_tests() {
    local repo_root="$1" collection test_name
    local -a collection_command
    DEFAULT_TESTS=()
    KNOWN_TESTS=()

    if [[ -n "${E2E_PYTHON:-}" ]]; then
        collection_command=("$E2E_PYTHON" -m pytest)
    else
        collection_command=(uv run --frozen pytest)
    fi
    if ! collection="$(
        cd "$repo_root" &&
            "${collection_command[@]}" --collect-only -q --disable-warnings --no-cov -m e2e tests/e2e 2>&1
    )"; then
        printf 'Failed to collect cloud E2E tests with pytest:\n%s\n' "$collection" >&2
        return 2
    fi

    while IFS= read -r test_name; do
        [[ -n "$test_name" ]] || continue
        KNOWN_TESTS+=("$test_name")
    done < <(
        printf '%s\n' "$collection" |
            sed -nE 's#^tests/e2e/(test_e2e_[a-zA-Z0-9_]+)\.py::.*#\1#p' |
            LC_ALL=C sort -u
    )
    DEFAULT_TESTS=("${KNOWN_TESTS[@]}")

    [[ ${#KNOWN_TESTS[@]} -gt 0 ]] || {
        printf 'Pytest collected no cloud E2E tests under %s/tests/e2e\n' "$repo_root" >&2
        return 2
    }
}

is_valid_test_name() {
    [[ "$1" =~ ^test_e2e_[a-zA-Z0-9_]+$ ]]
}

is_known_test() {
    local candidate="$1" test_name
    for test_name in "${KNOWN_TESTS[@]}"; do
        [[ "$candidate" == "$test_name" ]] && return 0
    done
    return 1
}

load_handle_tests() {
    local handle="$1" test_name

    validate_handle_directory "$handle" || return
    [[ -f "$handle/tests.txt" ]] || return 0
    TESTS=()
    while IFS= read -r test_name; do
        is_valid_test_name "$test_name" || {
            printf 'E2E handle contains invalid test name: %s\n' "$test_name" >&2
            return 2
        }
        TESTS+=("$test_name")
    done <"$handle/tests.txt"
    [[ ${#TESTS[@]} -gt 0 ]] || {
        printf 'E2E handle contains no tests: %s\n' "$handle" >&2
        return 2
    }
}

is_selected_test() {
    local candidate="$1" test_name
    for test_name in "${TESTS[@]+${TESTS[@]}}"; do
        [[ "$candidate" == "$test_name" ]] && return 0
    done
    return 1
}

write_status() {
    local path="$1" value="$2" temporary
    temporary="${path}.tmp.$$"
    printf '%s\n' "$value" >"$temporary"
    mv "$temporary" "$path"
}

show_status() {
    local handle="$1" test_name status attempt pid progress command_file
    local overall="PASSED" has_running=false has_attention=false

    [[ -d "$handle" ]] || {
        printf 'E2E handle does not exist: %s\n' "$handle" >&2
        return 2
    }
    load_handle_tests "$handle" || return

    for test_name in "${TESTS[@]}"; do
        status=$(cat "$handle/$test_name/status" 2>/dev/null || printf 'PENDING')
        case "$status" in
            PASSED) ;;
            PENDING | STARTING | RUNNING)
                has_running=true
                ;;
            *)
                has_attention=true
                ;;
        esac
    done
    if [[ "$has_running" == "true" ]]; then
        overall="RUNNING"
    elif [[ "$has_attention" == "true" ]]; then
        overall="NEEDS_ATTENTION"
    fi

    printf 'HANDLE\t%s\n' "$handle"
    printf 'OVERALL\t%s\n' "$overall"
    printf 'TEST\tSTATUS\tATTEMPT\tPID\tCOMMAND_FILE\tPROGRESS\n'
    for test_name in "${TESTS[@]}"; do
        status=$(cat "$handle/$test_name/status" 2>/dev/null || printf 'PENDING')
        attempt=$(cat "$handle/$test_name/latest-attempt" 2>/dev/null || printf '-')
        pid=$(cat "$handle/$test_name/pid" 2>/dev/null || printf '-')
        command_file="$handle/$test_name/attempt-${attempt}/command.sh"
        progress=$(tail -n 100 "$handle/$test_name/attempt-${attempt}/output.log" 2>/dev/null |
            grep '\[e2e\]' | tail -n 1 | tr '\t' ' ' || true)
        [[ -f "$command_file" ]] || command_file="-"
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$test_name" "$status" "$attempt" "$pid" "$command_file" "$progress"
    done
}

write_attempt_command() {
    local command_file="$1" attempt_dir="$2" test_name="$3"
    {
        printf '#!/usr/bin/env bash\n'
        printf 'set -o errexit -o nounset -o pipefail\n'
        printf 'cd %q\n' "$REPO_ROOT"
        printf 'export ARM_SUBSCRIPTION_ID=%q\n' "$ARM_SUBSCRIPTION_ID"
        printf 'export AZURE_SUBSCRIPTION_ID=%q\n' "$ARM_SUBSCRIPTION_ID"
        printf 'export AZURE_RESOURCE_GROUP=%q\n' "$AZURE_RESOURCE_GROUP"
        printf 'export AKS_CLUSTER_NAME=%q\n' "$AKS_CLUSTER_NAME"
        printf 'export AZUREML_WORKSPACE_NAME=%q\n' "$AZUREML_WORKSPACE_NAME"
        printf 'export AZUREML_COMPUTE=%q\n' "$AZUREML_COMPUTE"
        printf 'export AZURE_STORAGE_ACCOUNT_NAME=%q\n' "$AZURE_STORAGE_ACCOUNT_NAME"
        printf 'export E2E_VLA_STORAGE_ACCOUNT=%q\n' "$E2E_VLA_STORAGE_ACCOUNT"
        printf 'export KUBECONFIG=%q\n' "$KUBECONFIG"
        printf 'export XDG_CONFIG_HOME=%q\n' "$XDG_CONFIG_HOME"
        if [[ -n "${REQUESTS_CA_BUNDLE:-}" && -f "$REQUESTS_CA_BUNDLE" ]]; then
            printf 'export REQUESTS_CA_BUNDLE=%q\n' "$REQUESTS_CA_BUNDLE"
        else
            printf 'unset REQUESTS_CA_BUNDLE\n'
        fi
        printf 'export COVERAGE_FILE=%q\n' "$attempt_dir/.coverage"
        if [[ -n "${E2E_PYTHON:-}" ]]; then
            printf 'exec timeout %q %q -m pytest -vv -s -m e2e --no-cov --junitxml=%q %q\n' \
                "$WATCHDOG_SECONDS" \
                "$E2E_PYTHON" \
                "$attempt_dir/pytest.xml" \
                "tests/e2e/${test_name}.py"
        else
            printf 'exec timeout %q uv run --frozen pytest -vv -s -m e2e --no-cov --junitxml=%q %q\n' \
                "$WATCHDOG_SECONDS" \
                "$attempt_dir/pytest.xml" \
                "tests/e2e/${test_name}.py"
        fi
    } >"$command_file"
    chmod 700 "$command_file"
}

run_one() {
    local handle="$1" test_name="$2" test_dir attempt attempt_dir command_file output_log return_code status

    load_handle_config "$handle" || return
    discover_tests "$REPO_ROOT" || return
    is_known_test "$test_name" || {
        printf 'Unknown E2E test: %s\n' "$test_name" >&2
        return 2
    }

    test_dir="$handle/$test_name"
    mkdir -p "$test_dir"
    attempt=$(( $(cat "$test_dir/latest-attempt" 2>/dev/null || printf '0') + 1 ))
    attempt_dir="$test_dir/attempt-${attempt}"
    mkdir -p "$attempt_dir"
    printf '%s\n' "$attempt" >"$test_dir/latest-attempt"
    printf '%s\n' "$$" >"$test_dir/pid"
    write_status "$test_dir/status" "RUNNING"
    write_status "$attempt_dir/status" "RUNNING"
    append_handle_event "$handle" "attempt" "running" "$test_name attempt $attempt"

    command_file="$attempt_dir/command.sh"
    output_log="$attempt_dir/output.log"
    write_attempt_command "$command_file" "$attempt_dir" "$test_name"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$(date -u +%FT%TZ)" \
        "$test_name" \
        "$attempt" \
        "$$" \
        "$command_file" \
        "$output_log" >>"$handle/commands.tsv"

    set +o errexit
    "$command_file" >"$output_log" 2>&1
    return_code=$?
    set -o errexit
    printf '%s\n' "$return_code" >"$attempt_dir/exit-code"

    if grep -Eq '(^|[[:space:]])[1-9][0-9]* skipped([,[:space:]]|$)' "$output_log"; then
        status="FAILED_SETUP_SKIP"
    elif [[ "$return_code" -eq 0 ]]; then
        status="PASSED"
    elif [[ "$return_code" -eq 124 ]]; then
        status="TRANSIENT_WATCHDOG"
    elif grep -Eqi \
        'SkuNotAvailable|node-disruption|Status Code: 50[34]|upstream request timeout|upstream connect error|login\.microsoftonline\.com|UNEXPECTED_EOF_WHILE_READING|RemoteDisconnected|Timed out waiting for .* to (start|complete)|did not .* complete within|to start within' \
        "$output_log"; then
        status="TRANSIENT"
    else
        status="FAILED"
    fi

    write_status "$attempt_dir/status" "$status"
    write_status "$test_dir/status" "$status"
    printf '%s\n' "$status" >"$attempt_dir/result"
    append_handle_event "$handle" "attempt" "$status" "$test_name attempt $attempt"
    [[ "$status" == "PASSED" ]]
}

start_one() {
    local handle="$1" test_name="$2" test_dir pid status previous_attempt KUBECONFIG=""
    [[ -f "$handle/config.json" ]] || {
        printf 'Invalid E2E handle: %s\n' "$handle" >&2
        return 2
    }
    load_handle_config "$handle" || return
    discover_tests "$REPO_ROOT" || return
    is_known_test "$test_name" || {
        printf 'Unknown E2E test: %s\n' "$test_name" >&2
        return 2
    }
    [[ -n "$KUBECONFIG" && -f "$KUBECONFIG" ]] || {
        printf 'E2E handle kubeconfig is unavailable: %s\n' "${KUBECONFIG:-unset}" >&2
        return 2
    }
    test_dir="$handle/$test_name"
    mkdir -p "$test_dir"
    previous_attempt=$(cat "$test_dir/latest-attempt" 2>/dev/null || printf '0')
    printf '%s\n' "$previous_attempt" >"$test_dir/launch-previous-attempt"
    status=$(cat "$test_dir/status" 2>/dev/null || true)
    [[ "$status" != "RUNNING" && "$status" != "STARTING" ]] ||
        {
            printf '%s is already running\n' "$test_name" >&2
            return 1
        }
    write_status "$test_dir/status" "STARTING"
    nohup "$SCRIPT_PATH" --run-one "$handle" "$test_name" \
        >"$test_dir/launcher.log" 2>&1 < /dev/null &
    pid=$!
    printf '%s\n' "$pid" >"$test_dir/pid"
    record_process_identity "$pid" "$test_dir/process.json" "test-attempt" ||
        warn "Could not record process identity for $test_name"
    append_handle_event "$handle" "attempt-launch" "started" "$test_name"
}

print_latest_command() {
    local handle="$1" test_name="$2" test_dir previous_attempt attempt command_file flattened_command
    test_dir="$handle/$test_name"
    previous_attempt=$(cat "$test_dir/launch-previous-attempt" 2>/dev/null || printf '0')
    for _ in {1..100}; do
        attempt=$(cat "$test_dir/latest-attempt" 2>/dev/null || true)
        command_file="$test_dir/attempt-${attempt}/command.sh"
        [[ -n "$attempt" && "$attempt" -gt "$previous_attempt" && -f "$command_file" ]] && break
        sleep 0.1
    done
    [[ -n "${attempt:-}" && "$attempt" -gt "$previous_attempt" && -f "${command_file:-}" ]] ||
        {
            printf 'Command file was not created for %s\n' "$test_name" >&2
            return 1
        }
    flattened_command=$(sed '/^#!/d; /^set -o /d' "$command_file" | paste -sd ' ' -)
    printf 'COMMAND_FILE\t%s\t%s\n' "$test_name" "$command_file"
    printf 'COMMAND\t%s\t%s\n' "$test_name" "$flattened_command"
}

resubmit_one() {
    local handle="$1" test_name="$2" osmo_endpoint
    [[ -f "$handle/config.json" ]] || {
        printf 'Invalid E2E handle: %s\n' "$handle" >&2
        return 2
    }
    load_handle_tests "$handle" || return
    is_selected_test "$test_name" || {
        printf 'Test is not part of this E2E handle: %s\n' "$test_name" >&2
        return 2
    }
    if [[ "$test_name" == test_e2e_osmo_* ]]; then
        load_handle_config "$handle" || return
        osmo_endpoint="${OSMO_SERVICE_URL:-}"
        if [[ -z "$osmo_endpoint" ]]; then
            ensure_gateway "$handle"
            osmo_endpoint="http://localhost:${OSMO_GATEWAY_PORT}/"
        fi
        osmo login "$osmo_endpoint" --method dev --username admin >/dev/null ||
            fatal "OSMO CLI login failed"
        osmo profile set pool default >/dev/null ||
            fatal "OSMO default pool selection failed"
        osmo workflow list --count 1 --format-type json >/dev/null ||
            fatal "OSMO CLI workflow-list smoke test failed"
    fi
    start_one "$handle" "$test_name"
    print_latest_command "$handle" "$test_name"
    append_handle_event "$handle" "resubmit" "started" "$test_name"
    printf 'RESUBMITTED\t%s\n' "$test_name"
    printf 'HANDLE\t%s\n' "$handle"
    printf 'STATUS_COMMAND\t%s --status %s\n' "$SCRIPT_PATH" "$handle"
}

gateway_keepalive() {
    local handle="$1" port_forward_pid="" port_forward_identity
    port_forward_identity="$handle/osmo-gateway-port-forward.json"
    load_handle_config "$handle" || return

    # shellcheck disable=SC2329
    stop_port_forward() {
        stop_recorded_process "$port_forward_identity" "osmo-port-forward" || true
        exit 0
    }
    trap stop_port_forward TERM INT

    while true; do
        kubectl port-forward "svc/osmo-gateway" "${OSMO_GATEWAY_PORT}:80" -n osmo-control-plane \
            >>"$handle/osmo-gateway.log" 2>&1 &
        port_forward_pid=$!
        record_process_identity "$port_forward_pid" "$port_forward_identity" "osmo-port-forward" ||
            fatal "Could not record OSMO port-forward process identity"
        append_handle_event "$handle" "osmo-port-forward" "started" "PID $port_forward_pid"
        wait "$port_forward_pid" || true
        rm -f "$port_forward_identity"
        append_handle_event "$handle" "osmo-port-forward" "stopped" "PID $port_forward_pid"
        sleep 2
    done
}

ensure_gateway() {
    local handle="$1" gateway_pid="" gateway_identity
    gateway_identity="$handle/osmo-gateway.json"
    load_handle_config "$handle" || return
    if recorded_process_matches "$gateway_identity" "osmo-gateway"; then
        gateway_pid=$(jq -r '.pid' "$gateway_identity")
    else
        nohup "$SCRIPT_PATH" --gateway-keepalive "$handle" \
            >"$handle/osmo-gateway-launcher.log" 2>&1 < /dev/null &
        gateway_pid=$!
        record_process_identity "$gateway_pid" "$gateway_identity" "osmo-gateway" ||
            fatal "Could not record OSMO gateway process identity"
        append_handle_event "$handle" "osmo-gateway" "started" "PID $gateway_pid"
    fi
    for _ in {1..30}; do
        curl -fsS -o /dev/null "http://localhost:${OSMO_GATEWAY_PORT}" && return 0
        sleep 2
    done
    printf 'OSMO gateway is unavailable on localhost:%s\n' "$OSMO_GATEWAY_PORT" >&2
    return 1
}

cleanup_handle() {
    local handle="$1" test_name status attempt process_identity pid_file pid process_state
    validate_handle_directory "$handle" || return
    load_handle_tests "$handle" || return
    for test_name in "${TESTS[@]}"; do
        status=$(cat "$handle/$test_name/status" 2>/dev/null || printf 'PENDING')
        if [[ "$status" == "STARTING" || "$status" == "RUNNING" ]]; then
            process_identity="$handle/$test_name/process.json"
            pid_file="$handle/$test_name/pid"
            pid=$(cat "$pid_file" 2>/dev/null || true)
            if [[ -e "$pid_file" && ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
                printf 'Cannot clean up while %s has an invalid recorded PID\n' "$test_name" >&2
                return 1
            fi
            process_state=0
            classify_recorded_process "$process_identity" "test-attempt" "$pid" || process_state=$?
            case "$process_state" in
                0)
                    printf 'Cannot clean up while %s is %s\n' "$test_name" "$status" >&2
                    return 1
                    ;;
                2)
                    printf 'Cannot clean up while %s has a live or invalid process identity\n' \
                        "$test_name" >&2
                    return 1
                    ;;
            esac
            write_status "$handle/$test_name/status" "INTERRUPTED"
            attempt=$(cat "$handle/$test_name/latest-attempt" 2>/dev/null || true)
            if [[ -n "$attempt" && -d "$handle/$test_name/attempt-$attempt" ]]; then
                write_status "$handle/$test_name/attempt-$attempt/status" "INTERRUPTED"
                printf '%s\n' "INTERRUPTED" >"$handle/$test_name/attempt-$attempt/result"
            fi
            append_handle_event "$handle" "attempt" "interrupted" "$test_name stale $status state"
        fi
    done
    stop_recorded_process "$handle/osmo-gateway-port-forward.json" "osmo-port-forward" || true
    stop_recorded_process "$handle/osmo-gateway.json" "osmo-gateway" || true
    append_handle_event "$handle" "cleanup" "completed"
    printf 'CLEANED\t%s\n' "$handle"
}

case "${1:-}" in
    --status)
        [[ $# -eq 2 ]] || {
            printf 'Usage: %s --status HANDLE\n' "$(basename "$0")" >&2
            exit 2
        }
        show_status "$2"
        exit
        ;;
    --resubmit)
        [[ $# -eq 3 ]] || {
            printf 'Usage: %s --resubmit HANDLE TEST\n' "$(basename "$0")" >&2
            exit 2
        }
        resubmit_one "$2" "$3"
        exit
        ;;
    --cleanup)
        [[ $# -eq 2 ]] || {
            printf 'Usage: %s --cleanup HANDLE\n' "$(basename "$0")" >&2
            exit 2
        }
        cleanup_handle "$2"
        exit
        ;;
    --run-one)
        [[ $# -eq 3 ]] || exit 2
        run_one "$2" "$3"
        exit
        ;;
    --gateway-keepalive)
        [[ $# -eq 2 ]] || exit 2
        gateway_keepalive "$2"
        exit
        ;;
esac

repo_root_arg=""
previous_arg=""
for arg in "$@"; do
    if [[ "$previous_arg" == "--repo-root" ]]; then
        repo_root_arg="$arg"
        break
    fi
    previous_arg="$arg"
done
if [[ -n "$repo_root_arg" ]]; then
    REPO_ROOT="$(cd "$repo_root_arg" && pwd)"
else
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[[ -n "$REPO_ROOT" && -d "$REPO_ROOT/scripts/lib" ]] || {
    printf 'Run from a physical-ai-toolchain worktree or pass --repo-root.\n' >&2
    exit 1
}

show_help() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]
       $(basename "$0") --status HANDLE
       $(basename "$0") --resubmit HANDLE TEST
       $(basename "$0") --cleanup HANDLE

Validate Terraform and live compute, launch one detached attempt of the selected
cloud E2E tests, print a durable Copilot tracking handle, and exit. Tests are
discovered through pytest collection when --test is omitted.

OPTIONS:
    --repo-root DIR        Repository worktree (default: current Git worktree)
    --terraform-dir DIR    Terraform deployment directory
                           (default: <repo>/infrastructure/terraform)
    --run-root DIR         Handle storage
    --compute NAME         Azure ML compute (default: gpu-cluster)
    --watchdog SECONDS     Per-attempt timeout (default: 7200)
    --test NAME            Test to run; repeat for multiple tests
    --config-preview       Validate and print configuration without submissions
    -h, --help             Show this help message
EOF
}

terraform_dir="$REPO_ROOT/infrastructure/terraform"
run_root="${XDG_STATE_HOME:-$HOME/.local/state}/physical-ai-toolchain/e2e-runs/$(basename "$REPO_ROOT")"
compute="${AZUREML_COMPUTE:-gpu-cluster}"
watchdog=7200
config_preview=false
selected_tests=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) show_help; exit 0 ;;
        --repo-root) REPO_ROOT="$(cd "$2" && pwd)"; shift 2 ;;
        --terraform-dir) terraform_dir="$2"; shift 2 ;;
        --run-root) run_root="$2"; shift 2 ;;
        --compute) compute="$2"; shift 2 ;;
        --watchdog) watchdog="$2"; shift 2 ;;
        --test) selected_tests+=("$2"); shift 2 ;;
        --config-preview) config_preview=true; shift ;;
        *) fatal "Unknown option: $1" ;;
    esac
done

discover_tests "$REPO_ROOT"
TESTS=("${DEFAULT_TESTS[@]}")

if [[ ${#selected_tests[@]} -gt 0 ]]; then
    TESTS=()
    for selected_test in "${selected_tests[@]}"; do
        is_known_test "$selected_test" || fatal "Unknown E2E test: $selected_test"
        if ! is_selected_test "$selected_test"; then
            TESTS+=("$selected_test")
        fi
    done
fi

needs_osmo=false
for test_name in "${TESTS[@]}"; do
    if [[ "$test_name" == test_e2e_osmo_* ]]; then
        needs_osmo=true
        break
    fi
done

require_tools az curl jq kubectl osmo terraform timeout uv
[[ "$watchdog" =~ ^[1-9][0-9]*$ ]] || fatal "--watchdog must be a positive integer"

#------------------------------------------------------------------------------
# Validate Terraform and Live Targets
#------------------------------------------------------------------------------

tf_output=$(read_terraform_outputs "$terraform_dir")
resource_group=$(tf_require "$tf_output" "resource_group.value.name" "Resource group")
resource_group_id=$(tf_require "$tf_output" "resource_group.value.id" "Resource group ID")
aks_cluster=$(tf_require "$tf_output" "aks_cluster.value.name" "AKS cluster")
aks_cluster_id=$(tf_require "$tf_output" "aks_cluster.value.id" "AKS resource ID")
workspace=$(tf_require "$tf_output" "azureml_workspace.value.name" "Azure ML workspace")
workspace_id=$(tf_require "$tf_output" "azureml_workspace.value.id" "Azure ML workspace ID")
storage_account=$(tf_require "$tf_output" "storage_account.value.name" "Storage account")
storage_account_id=$(tf_require "$tf_output" "storage_account.value.id" "Storage account ID")
node_pools=$(printf '%s' "$tf_output" | jq -c '.node_pools.value // empty')
[[ -n "$node_pools" && "$node_pools" != "{}" && "$node_pools" != "null" ]] ||
    fatal "GPU node pool configuration not found in Terraform outputs"

az account show >/dev/null 2>&1 || fatal "Azure CLI is not authenticated; run 'az login'"
subscription_id=$(az account show --query id -o tsv)
normalized_subscription_id=$(printf '%s' "$subscription_id" | tr '[:upper:]' '[:lower:]')
normalized_aks_cluster_id=$(printf '%s' "$aks_cluster_id" | tr '[:upper:]' '[:lower:]')
[[ "$normalized_aks_cluster_id" == "/subscriptions/${normalized_subscription_id}/"* ]] ||
    fatal "Active Azure subscription does not match the Terraform AKS resource ID"

live_resource_group_id=$(az group show --name "$resource_group" --query id -o tsv)
normalized_live_resource_group_id=$(printf '%s' "$live_resource_group_id" | tr '[:upper:]' '[:lower:]')
normalized_resource_group_id=$(printf '%s' "$resource_group_id" | tr '[:upper:]' '[:lower:]')
[[ "$normalized_live_resource_group_id" == "$normalized_resource_group_id" ]] ||
    fatal "Live resource group ID does not match Terraform outputs"
live_aks_id=$(az aks show --resource-group "$resource_group" --name "$aks_cluster" --query id -o tsv)
[[ "$(printf '%s' "$live_aks_id" | tr '[:upper:]' '[:lower:]')" == "$normalized_aks_cluster_id" ]] ||
    fatal "Live AKS resource ID does not match Terraform outputs"
live_workspace_id=$(az ml workspace show \
    --resource-group "$resource_group" --name "$workspace" --query id -o tsv)
normalized_live_workspace_id=$(printf '%s' "$live_workspace_id" | tr '[:upper:]' '[:lower:]')
normalized_workspace_id=$(printf '%s' "$workspace_id" | tr '[:upper:]' '[:lower:]')
[[ "$normalized_live_workspace_id" == "$normalized_workspace_id" ]] ||
    fatal "Live Azure ML workspace ID does not match Terraform outputs"
live_storage_account_id=$(az storage account show \
    --resource-group "$resource_group" --name "$storage_account" --query id -o tsv)
normalized_live_storage_account_id=$(printf '%s' "$live_storage_account_id" | tr '[:upper:]' '[:lower:]')
normalized_storage_account_id=$(printf '%s' "$storage_account_id" | tr '[:upper:]' '[:lower:]')
[[ "$normalized_live_storage_account_id" == "$normalized_storage_account_id" ]] ||
    fatal "Live storage account ID does not match Terraform outputs"

live_node_pools=$(az aks nodepool list \
    --resource-group "$resource_group" --cluster-name "$aks_cluster" -o json)
while IFS= read -r pool_name; do
    expected_vm_size=$(printf '%s' "$node_pools" | jq -r --arg pool "$pool_name" '.[$pool].vm_size')
    live_vm_size=$(printf '%s' "$live_node_pools" | jq -r --arg pool "$pool_name" \
        '.[] | select(.name == $pool) | .vmSize')
    [[ "$live_vm_size" == "$expected_vm_size" ]] ||
        fatal "AKS pool $pool_name is absent or does not match Terraform VM size $expected_vm_size"
    printf '%s' "$live_node_pools" | jq -e --arg pool "$pool_name" '
        .[] | select(.name == $pool) |
        ((.count // 0) > 0) or
        (.enableAutoScaling == true and (.maxCount // 0) > 0)
    ' >/dev/null || fatal "AKS pool $pool_name cannot provide GPU capacity"
done < <(printf '%s' "$node_pools" | jq -r 'keys[]')

aml_compute=$(az ml compute show \
    --resource-group "$resource_group" --workspace-name "$workspace" --name "$compute" -o json)
printf '%s' "$aml_compute" | jq -e '
    .type == "amlcompute" and
    .provisioning_state == "Succeeded" and
    (.max_instances // 0) > 0
' >/dev/null || fatal "Azure ML compute $compute is unavailable or cannot scale"

if [[ "$needs_osmo" == "true" ]]; then
    container_exists=$(az storage container exists \
        --account-name "$storage_account" --auth-mode login --name osmo --query exists -o tsv)
    [[ "$container_exists" == "true" ]] ||
        fatal "Terraform storage account $storage_account does not contain the osmo container"
fi

export ARM_SUBSCRIPTION_ID="$subscription_id"
export AZURE_SUBSCRIPTION_ID="$subscription_id"
export AZURE_RESOURCE_GROUP="$resource_group"
export AKS_CLUSTER_NAME="$aks_cluster"
export AZUREML_WORKSPACE_NAME="$workspace"
export AZUREML_COMPUTE="$compute"
export AZURE_STORAGE_ACCOUNT_NAME="$storage_account"
export E2E_VLA_STORAGE_ACCOUNT="$storage_account"
export WATCHDOG_SECONDS="$watchdog"

if [[ "$config_preview" == "true" ]]; then
    section "Configuration Preview"
    print_kv "Repository" "$REPO_ROOT"
    print_kv "Terraform Directory" "$terraform_dir"
    print_kv "Resource Group" "$resource_group"
    print_kv "AKS Cluster" "$aks_cluster"
    print_kv "AKS GPU Pools" "$(printf '%s' "$node_pools" | jq -r 'keys | join(", ")')"
    print_kv "Azure ML Workspace" "$workspace"
    print_kv "Azure ML Compute" "$compute"
    print_kv "Storage Account" "$storage_account"
    print_kv "Tests" "$(printf '%s\n' "${TESTS[@]}" | paste -sd, -)"
    print_kv "Watchdog" "$watchdog"
    exit 0
fi

#------------------------------------------------------------------------------
# Launch
#------------------------------------------------------------------------------

mkdir -p "$run_root"
handle="$run_root/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$handle"
chmod 700 "$handle"
# OSMO stores its active endpoint in login.yaml; isolate it so concurrent handles cannot redirect each other.
export XDG_CONFIG_HOME="$handle/xdg-config"
mkdir -p "$XDG_CONFIG_HOME"
chmod 700 "$XDG_CONFIG_HOME"
export OSMO_GATEWAY_PORT=$((20000 + $$ % 20000))
while (echo >/dev/tcp/127.0.0.1/"$OSMO_GATEWAY_PORT") 2>/dev/null; do
    OSMO_GATEWAY_PORT=$((OSMO_GATEWAY_PORT + 1))
done
printf '%s\n' "${TESTS[@]}" >"$handle/tests.txt"
connect_aks "$resource_group" "$aks_cluster" "$handle/kubeconfig" "$aks_cluster"
export KUBECONFIG="$handle/kubeconfig"
write_handle_config "$handle"
append_handle_event "$handle" "handle" "created"
printf '%s\n' "$tf_output" >"$handle/terraform-output.json"
printf '%s\n' "$live_node_pools" >"$handle/live-node-pools.json"
printf '%s\n' "$aml_compute" >"$handle/azureml-compute.json"
chmod 600 "$handle/terraform-output.json" "$handle/live-node-pools.json" "$handle/azureml-compute.json"
{
    printf 'STARTED_AT\t%s\n' "$(date -u +%FT%TZ)"
    printf 'REPOSITORY\t%s\n' "$REPO_ROOT"
    printf 'TERRAFORM_DIRECTORY\t%s\n' "$terraform_dir"
    printf 'SUBSCRIPTION\t%s\n' "$subscription_id"
    printf 'RESOURCE_GROUP\t%s\n' "$resource_group"
    printf 'AKS_CLUSTER\t%s\n' "$aks_cluster"
    printf 'AZUREML_WORKSPACE\t%s\n' "$workspace"
    printf 'AZUREML_COMPUTE\t%s\n' "$compute"
    printf 'STORAGE_ACCOUNT\t%s\n' "$storage_account"
    printf 'TESTS\t%s\n' "$(printf '%s\n' "${TESTS[@]}" | paste -sd, -)"
} >"$handle/manifest.tsv"
manifest_json="$handle/manifest.json"
jq -n \
    --arg started_at "$(date -u +%FT%TZ)" \
    --arg repository "$REPO_ROOT" \
    --arg terraform_directory "$terraform_dir" \
    --arg subscription "$subscription_id" \
    --arg resource_group "$resource_group" \
    --arg aks_cluster "$aks_cluster" \
    --arg azureml_workspace "$workspace" \
    --arg azureml_compute "$compute" \
    --arg storage_account "$storage_account" \
    --arg tests "$(printf '%s\n' "${TESTS[@]}" | paste -sd, -)" \
    '{
        schemaVersion: 1,
        startedAt: $started_at,
        repository: $repository,
        terraformDirectory: $terraform_directory,
        subscription: $subscription,
        resourceGroup: $resource_group,
        aksCluster: $aks_cluster,
        azuremlWorkspace: $azureml_workspace,
        azuremlCompute: $azureml_compute,
        storageAccount: $storage_account,
        tests: ($tests | split(","))
    }' >"$manifest_json"
chmod 600 "$manifest_json" "$handle/manifest.tsv"
printf 'TIMESTAMP\tTEST\tATTEMPT\tPID\tCOMMAND_FILE\tOUTPUT_LOG\n' >"$handle/commands.tsv"

if [[ "$needs_osmo" == "true" ]]; then
    osmo_endpoint="${OSMO_SERVICE_URL:-}"
    if [[ -z "$osmo_endpoint" ]]; then
        ensure_gateway "$handle" || fatal "OSMO gateway is unavailable on localhost:${OSMO_GATEWAY_PORT}"
        osmo_endpoint="http://localhost:${OSMO_GATEWAY_PORT}/"
    fi
    osmo login "$osmo_endpoint" --method dev --username admin >/dev/null ||
        fatal "OSMO CLI login failed"
    osmo profile set pool default >/dev/null ||
        fatal "OSMO default pool selection failed"
    osmo_credentials=$(osmo credential --format-type json list)
    printf '%s' "$osmo_credentials" | jq -e '
        any(.credentials[]?; .cred_name == "huggingface" and .cred_type == "GENERIC")
    ' >/dev/null || fatal "OSMO Hugging Face credential is missing or has the wrong type"
    osmo workflow list --count 1 --format-type json >/dev/null ||
        fatal "OSMO CLI workflow-list smoke test failed"
fi

for test_name in "${TESTS[@]}"; do
    start_one "$handle" "$test_name"
done
for test_name in "${TESTS[@]}"; do
    print_latest_command "$handle" "$test_name"
done

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "E2E Attempts Launched"
print_kv "E2E_HANDLE" "$handle"
print_kv "E2E_STATUS_COMMAND" "$SCRIPT_PATH --status $handle"
print_kv "E2E_RESUBMIT_COMMAND" "$SCRIPT_PATH --resubmit $handle <test-name>"
print_kv "E2E_CLEANUP_COMMAND" "$SCRIPT_PATH --cleanup $handle"
printf 'E2E_HANDLE=%s\n' "$handle"
printf 'E2E_STATUS_COMMAND=%s --status %s\n' "$SCRIPT_PATH" "$handle"
printf 'E2E_RESUBMIT_COMMAND=%s --resubmit %s <test-name>\n' "$SCRIPT_PATH" "$handle"
printf 'E2E_CLEANUP_COMMAND=%s --cleanup %s\n' "$SCRIPT_PATH" "$handle"
