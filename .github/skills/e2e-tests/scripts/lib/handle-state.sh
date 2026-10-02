#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Secure E2E handle state, event, and child-process helpers.
# cspell:ignore lstart

file_owner_id() {
    local path="$1"
    if stat -f '%u' "$path" >/dev/null 2>&1; then
        stat -f '%u' "$path"
    else
        stat -c '%u' "$path"
    fi
}

file_mode() {
    local path="$1"
    if stat -f '%Lp' "$path" >/dev/null 2>&1; then
        stat -f '%Lp' "$path"
    else
        stat -c '%a' "$path"
    fi
}

validate_handle_directory() {
    local handle="$1" symlink

    [[ -d "$handle" && ! -L "$handle" ]] || {
        printf 'E2E handle is not a regular directory: %s\n' "$handle" >&2
        return 2
    }
    [[ "$(file_owner_id "$handle")" == "$(id -u)" ]] || {
        printf 'E2E handle is not owned by the current user: %s\n' "$handle" >&2
        return 2
    }
    [[ "$(file_mode "$handle")" == "700" ]] || {
        printf 'E2E handle permissions must be 700: %s\n' "$handle" >&2
        return 2
    }
    symlink=$(find "$handle" -type l -print 2>/dev/null)
    [[ -z "$symlink" ]] || {
        printf 'E2E handle contains a symbolic link: %s\n' "$symlink" >&2
        return 2
    }
}

validate_private_file() {
    local path="$1" description="$2"

    [[ -f "$path" && ! -L "$path" ]] || {
        printf '%s is not a regular file: %s\n' "$description" "$path" >&2
        return 2
    }
    [[ "$(file_owner_id "$path")" == "$(id -u)" ]] || {
        printf '%s is not owned by the current user: %s\n' "$description" "$path" >&2
        return 2
    }
    [[ "$(file_mode "$path")" == "600" ]] || {
        printf '%s permissions must be 600: %s\n' "$description" "$path" >&2
        return 2
    }
}

load_handle_config() {
    local handle="$1" config_file schema_version
    config_file="$handle/config.json"

    validate_handle_directory "$handle" || return
    validate_private_file "$config_file" "E2E handle configuration" || return
    command -v jq >/dev/null 2>&1 || {
        printf 'Required tool not found: jq\n' >&2
        return 2
    }

    schema_version=$(jq -er '.schemaVersion' "$config_file") || {
        printf 'E2E handle configuration has no schema version: %s\n' "$config_file" >&2
        return 2
    }
    [[ "$schema_version" == "1" ]] || {
        printf 'Unsupported E2E handle schema version: %s\n' "$schema_version" >&2
        return 2
    }

    REPO_ROOT=$(jq -er '.repositoryRoot | select(type == "string" and length > 0)' "$config_file")
    ARM_SUBSCRIPTION_ID=$(jq -er '.subscriptionId | select(type == "string" and length > 0)' "$config_file")
    AZURE_RESOURCE_GROUP=$(jq -er '.resourceGroup | select(type == "string" and length > 0)' "$config_file")
    AKS_CLUSTER_NAME=$(jq -er '.aksCluster | select(type == "string" and length > 0)' "$config_file")
    AZUREML_WORKSPACE_NAME=$(jq -er '.azuremlWorkspace | select(type == "string" and length > 0)' "$config_file")
    AZUREML_COMPUTE=$(jq -er '.azuremlCompute | select(type == "string" and length > 0)' "$config_file")
    AZURE_STORAGE_ACCOUNT_NAME=$(jq -er '.storageAccount | select(type == "string" and length > 0)' "$config_file")
    E2E_VLA_STORAGE_ACCOUNT=$(jq -er '.vlaStorageAccount | select(type == "string" and length > 0)' "$config_file")
    KUBECONFIG=$(jq -er '.kubeconfig | select(type == "string" and length > 0)' "$config_file")
    XDG_CONFIG_HOME=$(jq -er '.xdgConfigHome | select(type == "string" and length > 0)' "$config_file")
    OSMO_GATEWAY_PORT=$(jq -er '.osmoGatewayPort | select(type == "number")' "$config_file")
    WATCHDOG_SECONDS=$(jq -er '.watchdogSeconds | select(type == "number")' "$config_file")
    REQUESTS_CA_BUNDLE=$(jq -r '.requestsCaBundle // ""' "$config_file")
    E2E_PYTHON=$(jq -r '.e2ePython // ""' "$config_file")
    OSMO_SERVICE_URL=$(jq -r '.osmoServiceUrl // ""' "$config_file")

    export REPO_ROOT ARM_SUBSCRIPTION_ID AZURE_RESOURCE_GROUP AKS_CLUSTER_NAME
    export AZUREML_WORKSPACE_NAME AZUREML_COMPUTE AZURE_STORAGE_ACCOUNT_NAME
    export E2E_VLA_STORAGE_ACCOUNT KUBECONFIG XDG_CONFIG_HOME OSMO_GATEWAY_PORT
    export WATCHDOG_SECONDS REQUESTS_CA_BUNDLE E2E_PYTHON OSMO_SERVICE_URL
}

write_handle_config() {
    local handle="$1" config_file temporary
    config_file="$handle/config.json"
    temporary="${config_file}.tmp.$$"

    jq -n \
        --arg repository_root "$REPO_ROOT" \
        --arg subscription_id "$ARM_SUBSCRIPTION_ID" \
        --arg resource_group "$AZURE_RESOURCE_GROUP" \
        --arg aks_cluster "$AKS_CLUSTER_NAME" \
        --arg azureml_workspace "$AZUREML_WORKSPACE_NAME" \
        --arg azureml_compute "$AZUREML_COMPUTE" \
        --arg storage_account "$AZURE_STORAGE_ACCOUNT_NAME" \
        --arg vla_storage_account "$E2E_VLA_STORAGE_ACCOUNT" \
        --arg kubeconfig "$KUBECONFIG" \
        --arg xdg_config_home "$XDG_CONFIG_HOME" \
        --arg requests_ca_bundle "${REQUESTS_CA_BUNDLE:-}" \
        --arg e2e_python "${E2E_PYTHON:-}" \
        --arg osmo_service_url "${OSMO_SERVICE_URL:-}" \
        --argjson osmo_gateway_port "$OSMO_GATEWAY_PORT" \
        --argjson watchdog_seconds "$WATCHDOG_SECONDS" \
        '{
            schemaVersion: 1,
            repositoryRoot: $repository_root,
            subscriptionId: $subscription_id,
            resourceGroup: $resource_group,
            aksCluster: $aks_cluster,
            azuremlWorkspace: $azureml_workspace,
            azuremlCompute: $azureml_compute,
            storageAccount: $storage_account,
            vlaStorageAccount: $vla_storage_account,
            kubeconfig: $kubeconfig,
            xdgConfigHome: $xdg_config_home,
            osmoGatewayPort: $osmo_gateway_port,
            watchdogSeconds: $watchdog_seconds,
            requestsCaBundle: ($requests_ca_bundle | select(length > 0)),
            e2ePython: ($e2e_python | select(length > 0)),
            osmoServiceUrl: ($osmo_service_url | select(length > 0))
        }' >"$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$config_file"
}

append_handle_event() {
    local handle="$1" event="$2" status="$3" detail="${4:-}"
    local event_file="$handle/events.jsonl"

    if [[ -e "$event_file" || -L "$event_file" ]]; then
        validate_private_file "$event_file" "E2E event log" || return
    else
        install -m 600 /dev/null "$event_file"
    fi
    jq -cn \
        --arg timestamp "$(date -u +%FT%TZ)" \
        --arg event "$event" \
        --arg status "$status" \
        --arg detail "$detail" \
        '{timestamp: $timestamp, event: $event, status: $status, detail: $detail}' >>"$event_file"
}

record_process_identity() {
    local pid="$1" identity_file="$2" role="$3"
    local command started temporary

    command=$(ps -p "$pid" -o command= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    started=$(ps -p "$pid" -o lstart= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    [[ -n "$command" && -n "$started" ]] || return 1
    temporary="${identity_file}.tmp.$$"
    jq -n \
        --argjson pid "$pid" \
        --arg role "$role" \
        --arg command "$command" \
        --arg started "$started" \
        '{schemaVersion: 1, pid: $pid, role: $role, command: $command, started: $started}' \
        >"$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$identity_file"
}

recorded_process_matches() {
    local identity_file="$1" expected_role="$2"
    local schema_version pid role expected_command expected_started live_command live_started

    [[ -f "$identity_file" && ! -L "$identity_file" ]] || return 1
    validate_private_file "$identity_file" "Process identity" || return
    schema_version=$(jq -er '.schemaVersion | select(. == 1)' "$identity_file") || return 2
    pid=$(jq -er '.pid | select(type == "number")' "$identity_file") || return 2
    role=$(jq -er '.role | select(type == "string")' "$identity_file") || return 2
    expected_command=$(jq -er '.command | select(type == "string")' "$identity_file") || return 2
    expected_started=$(jq -er '.started | select(type == "string")' "$identity_file") || return 2
    [[ "$schema_version" == "1" ]] || return 2
    [[ "$role" == "$expected_role" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    live_command=$(ps -p "$pid" -o command= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    live_started=$(ps -p "$pid" -o lstart= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    [[ "$live_command" == "$expected_command" && "$live_started" == "$expected_started" ]]
}

classify_recorded_process() {
    local identity_file="$1" expected_role="$2" fallback_pid="${3:-}"
    local schema_version pid role expected_command expected_started live_command live_started

    if [[ ! -f "$identity_file" || -L "$identity_file" ]]; then
        if [[ "$fallback_pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$fallback_pid" 2>/dev/null; then
            return 2
        fi
        return 1
    fi
    validate_private_file "$identity_file" "Process identity" || return 2
    schema_version=$(jq -er '.schemaVersion | select(. == 1)' "$identity_file") || return 2
    pid=$(jq -er '.pid | select(type == "number" and . > 0)' "$identity_file") || return 2
    role=$(jq -er '.role | select(type == "string")' "$identity_file") || return 2
    expected_command=$(jq -er '.command | select(type == "string" and length > 0)' "$identity_file") || return 2
    expected_started=$(jq -er '.started | select(type == "string" and length > 0)' "$identity_file") || return 2
    [[ "$schema_version" == "1" && "$role" == "$expected_role" ]] || return 2

    if [[ "$fallback_pid" =~ ^[1-9][0-9]*$ && "$fallback_pid" != "$pid" ]] &&
        kill -0 "$fallback_pid" 2>/dev/null; then
        return 2
    fi
    kill -0 "$pid" 2>/dev/null || return 1
    live_command=$(ps -p "$pid" -o command= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    live_started=$(ps -p "$pid" -o lstart= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    [[ -n "$live_command" && -n "$live_started" ]] || return 2
    [[ "$live_command" == "$expected_command" && "$live_started" == "$expected_started" ]] || return 2
    return 0
}

stop_recorded_process() {
    local identity_file="$1" expected_role="$2"
    local schema_version pid role expected_command expected_started live_command live_started

    [[ -f "$identity_file" && ! -L "$identity_file" ]] || return 0
    validate_private_file "$identity_file" "Process identity" || return
    schema_version=$(jq -er '.schemaVersion | select(. == 1)' "$identity_file") || return 2
    pid=$(jq -er '.pid | select(type == "number")' "$identity_file") || return 2
    role=$(jq -er '.role | select(type == "string")' "$identity_file") || return 2
    expected_command=$(jq -er '.command | select(type == "string")' "$identity_file") || return 2
    expected_started=$(jq -er '.started | select(type == "string")' "$identity_file") || return 2
    [[ "$schema_version" == "1" ]] || return 2
    [[ "$role" == "$expected_role" ]] || {
        warn "Refusing to stop PID $pid because its recorded role is '$role', not '$expected_role'"
        return 1
    }
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$identity_file"
        return 0
    fi
    live_command=$(ps -p "$pid" -o command= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    live_started=$(ps -p "$pid" -o lstart= 2>/dev/null | sed -E 's/^[[:space:]]+//')
    if [[ "$live_command" != "$expected_command" || "$live_started" != "$expected_started" ]]; then
        warn "Refusing to stop PID $pid because its live process identity does not match the handle"
        return 1
    fi
    kill "$pid"
    wait "$pid" 2>/dev/null || true
    rm -f "$identity_file"
}
