#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Exercise the uv bootstrap with controlled transfer responses and local archives.
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
test_root="$(mktemp -d)"
trap 'rm -rf -- "$test_root"' EXIT

mkdir -p "$test_root/uv-x86_64-unknown-linux-gnu"
printf '#!/usr/bin/env bash\nexit 0\n' > "$test_root/uv-x86_64-unknown-linux-gnu/uv"
tar -czf "$test_root/valid.tar.gz" -C "$test_root" uv-x86_64-unknown-linux-gnu
printf 'not an archive' > "$test_root/invalid.tar.gz"
valid_hash="$(sha256sum "$test_root/valid.tar.gz")"
valid_hash="${valid_hash%% *}"
invalid_hash="$(sha256sum "$test_root/invalid.tar.gz")"
invalid_hash="${invalid_hash%% *}"

command() {
    if [[ "${1:-}" == -v && "${2:-}" == uv ]]; then
        return 1
    fi
    builtin command "$@"
}

sleep() { [[ "$1" == 2 ]]; }

curl() {
    local output="" previous="" code attempt
    for argument in "$@"; do
        if [[ "$previous" == -o ]]; then
            output="$argument"
        fi
        previous="$argument"
    done
    [[ "$*" == *"--max-time 35"* && "$*" == *"--connect-timeout 10"* ]]
    [[ "$output" == */uv.tar.gz ]]
    printf 'attempt\n' >> "$attempt_file"
    attempt="$(wc -l < "$attempt_file")"
    IFS=, read -r -a codes <<< "$responses"
    code="${codes[attempt - 1]}"
    case "$code" in
        200)
            cp "$fixture" "$output"
            printf '200'
            return 0
            ;;
        timeout)
            printf '000'
            return 28
            ;;
        connection)
            printf '000'
            return 7
            ;;
        tls_error)
            printf '000'
            return 60
            ;;
        408 | 429 | 5?? | 404)
            printf '%s' "$code"
            return 22
            ;;
        *) echo "Unexpected test response: $code" >&2; return 1 ;;
    esac
}

# Source with a domain argument so only the bootstrap function runs.
source "$repo_root/shared/ci/smoke-import.sh" rl

run_case() {
    local name="$1" sequence="$2" archive="$3" hash="$4" expected_status="$5" expected_attempts="$6"
    local home_dir="$test_root/$name"
    mkdir -p "$home_dir"
    : > "$home_dir/attempts"
    if (
        HOME="$home_dir"
        responses="$sequence"
        fixture="$archive"
        attempt_file="$home_dir/attempts"
        UV_SHA256="$hash"
        ensure_uv
    ) > "$home_dir/output" 2>&1; then
        status=0
    else
        status=$?
    fi
    [[ "$status" -eq "$expected_status" ]] || {
        echo "$name: expected exit $expected_status, got $status" >&2
        return 1
    }
    [[ "$(wc -l < "$home_dir/attempts")" -eq "$expected_attempts" ]] || {
        echo "$name: unexpected transfer attempt count" >&2
        return 1
    }
    if [[ "$expected_status" -eq 0 ]]; then
        [[ -x "$home_dir/.local/bin/uv" ]]
    else
        [[ ! -e "$home_dir/.local/bin/uv" ]]
    fi
}

run_case transient '503,429,200' "$test_root/valid.tar.gz" "$valid_hash" 0 3
run_case request_timeout '408,200' "$test_root/valid.tar.gz" "$valid_hash" 0 2
run_case timeout 'timeout,200' "$test_root/valid.tar.gz" "$valid_hash" 0 2
run_case connection 'connection,200' "$test_root/valid.tar.gz" "$valid_hash" 0 2
run_case server_error '507,200' "$test_root/valid.tar.gz" "$valid_hash" 0 2
run_case persistent '503,503,503' "$test_root/valid.tar.gz" "$valid_hash" 1 3
run_case not_found '404,200' "$test_root/valid.tar.gz" "$valid_hash" 1 1
run_case tls_failure 'tls_error,200' "$test_root/valid.tar.gz" "$valid_hash" 1 1
run_case bad_hash '200,200' "$test_root/valid.tar.gz" "$(printf '%064d' 0)" 1 1
run_case bad_archive '200,200' "$test_root/invalid.tar.gz" "$invalid_hash" 1 1

echo 'uv bootstrap transfer and archive checks passed'
