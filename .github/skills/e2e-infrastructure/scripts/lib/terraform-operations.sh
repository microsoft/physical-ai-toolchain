#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Shared Terraform retry and destroy-diagnostic helpers.
# shellcheck disable=SC2154

extract_prevent_destroy_addresses() {
    local plan_file="$1"

    jq -rs '
        [
            .[] |
            select(
                .type == "diagnostic" and
                .diagnostic.summary == "Instance cannot be destroyed"
            ) |
            .diagnostic.detail |
            capture("^Resource (?<address>[^ ]+) has\\s+lifecycle\\.prevent_destroy set").address
        ] |
        unique[]
    ' "$plan_file"
}

run_terraform_operation_with_retries() {
    local operation="$1" attempt

    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        info "Terraform $operation attempt $attempt of $max_attempts"
        record_operation_event "terraform-$operation" "attempt" "$attempt of $max_attempts"
        if terraform_operation_attempt "$operation"; then
            record_operation_event "terraform-$operation" "succeeded" "attempt $attempt"
            return
        fi
        if [[ "$attempt" -lt "$max_attempts" ]]; then
            if [[ "$operation" == "apply" ]]; then
                reconcile_failed_managed_redis
            fi
            warn "Terraform $operation failed; retrying in $((attempt * 30)) seconds"
            sleep "$((attempt * 30))"
        fi
    done

    record_operation_event "terraform-$operation" "failed" "$max_attempts attempts"
    fatal "Terraform $operation failed after $max_attempts attempts"
}
