#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Shared configuration and validation for LeRobot workflow scripts.

set -euo pipefail

LEROBOT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly LEROBOT_SCRIPT_DIR
LEROBOT_DIR="$(dirname "${LEROBOT_SCRIPT_DIR}")"
readonly LEROBOT_DIR
LEROBOT_REPO_ROOT="$(
  git -C "${LEROBOT_DIR}" rev-parse --show-toplevel 2>/dev/null || dirname "${LEROBOT_DIR}"
)"
readonly LEROBOT_REPO_ROOT
# shellcheck disable=SC2034 # consumed by scripts that source common.sh (build_container.sh)
readonly GPU_OFFLOAD_DIR="${LEROBOT_REPO_ROOT}/gpu-offload"

load_env() {
  local env_file
  local -a env_files=(
    "${LEROBOT_DIR}/config/so101.env"
    "${LEROBOT_SCRIPT_DIR}/.env"
    "${LEROBOT_REPO_ROOT}/.env"
    "${LEROBOT_REPO_ROOT}/.env.local"
  )

  for env_file in "${env_files[@]}"; do
    if [[ -f "${env_file}" ]]; then
      set -a
      # shellcheck disable=SC1090
      source "${env_file}"
      set +a
    fi
  done
}

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  command -v "${command_name}" &>/dev/null || \
    die "'${command_name}' is required; run this script in the LeRobot container"
}

require_value() {
  local option_name="$1"
  local option_value="${2:-}"
  [[ -n "${option_value}" ]] || die "${option_name} requires a value"
}

require_positive_integer() {
  local option_name="$1"
  local option_value="$2"
  [[ "${option_value}" =~ ^[1-9][0-9]*$ ]] || \
    die "${option_name} must be a positive integer"
}

pinned_version() {
  local version
  version="$(sed -n 's/^LEROBOT_VERSION=//p' "${LEROBOT_DIR}/.lerobot-version")"
  [[ -n "${version}" ]] || die "LEROBOT_VERSION is missing from ${LEROBOT_DIR}/.lerobot-version"
  printf '%s\n' "${version#v}"
}

load_env
