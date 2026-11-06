#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Destructive confirmation and structured operation evidence helpers.
# cspell:ignore tfvar
# shellcheck disable=SC2154

operation_id=""
operation_started_at=""
operation_evidence_dir=""
operation_event_file=""

initialize_operation_evidence() {
    [[ "$config_preview" == "false" ]] || return 0

    operation_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    operation_started_at="$(date -u +%FT%TZ)"
    operation_evidence_dir="$generated_dir/$environment/e2e-operations/$operation_id"
    operation_event_file="$operation_evidence_dir/events.jsonl"
    mkdir -p "$operation_evidence_dir"
    chmod 700 "$operation_evidence_dir"
    record_operation_event "operation" "started" "$action"
}

record_operation_event() {
    local event="$1" status="$2" detail="${3:-}"

    [[ -n "$operation_event_file" ]] || return 0
    jq -cn \
        --arg timestamp "$(date -u +%FT%TZ)" \
        --arg operation_id "$operation_id" \
        --arg action "$action" \
        --arg event "$event" \
        --arg status "$status" \
        --arg detail "$detail" \
        '{
            timestamp: $timestamp,
            operationId: $operation_id,
            action: $action,
            event: $event,
            status: $status,
            detail: $detail
        }' >>"$operation_event_file"
    chmod 600 "$operation_event_file"
}

finalize_operation_evidence() {
    local exit_code="$1" completed_at status

    [[ -n "$operation_evidence_dir" ]] || return 0
    completed_at="$(date -u +%FT%TZ)"
    if [[ "$exit_code" -eq 0 ]]; then
        status="succeeded"
    else
        status="failed"
    fi
    record_operation_event "operation" "$status" "exit code $exit_code"
    jq -n \
        --arg operation_id "$operation_id" \
        --arg action "$action" \
        --arg status "$status" \
        --arg started_at "$operation_started_at" \
        --arg completed_at "$completed_at" \
        --arg repository "$repo_root" \
        --arg terraform_directory "$tf_dir" \
        --arg environment "$environment" \
        --arg instance "$(read_tfvar instance 2>/dev/null || true)" \
        --arg resource_group "rg-${resource_prefix}-${environment}-$(read_tfvar instance 2>/dev/null || true)" \
        --argjson exit_code "$exit_code" \
        '{
            schemaVersion: 1,
            operationId: $operation_id,
            action: $action,
            status: $status,
            exitCode: $exit_code,
            startedAt: $started_at,
            completedAt: $completed_at,
            repository: $repository,
            terraformDirectory: $terraform_directory,
            environment: $environment,
            instance: $instance,
            resourceGroup: $resource_group
        }' >"$operation_evidence_dir/summary.json"
    chmod 600 "$operation_evidence_dir/summary.json"
}

require_undeploy_confirmation() {
    local actual_resource_group="$1"
    local expected_resource_group

    expected_resource_group="rg-${resource_prefix}-${environment}-$(read_tfvar instance)"
    if [[ -n "$actual_resource_group" && "$actual_resource_group" != "$expected_resource_group" ]]; then
        fatal "Terraform resource group '$actual_resource_group' does not match expected '$expected_resource_group'"
    fi
    [[ "$confirm_resource_group" == "$expected_resource_group" ]] ||
        fatal "Undeploy requires --confirm-resource-group '$expected_resource_group'"
    record_operation_event "confirmation" "validated" "$expected_resource_group"
}
