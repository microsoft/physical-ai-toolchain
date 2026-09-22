#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Update the pinned LeRobot PyPI package version.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: update_lerobot_version.sh VERSION

Update the pinned LeRobot version in pyproject.toml and .lerobot-version,
then re-lock dependencies. Review the release notes, rebuild both
architectures, run the workflows you use, and then commit pyproject.toml,
uv.lock, and .lerobot-version together.
EOF
}

main() {
  local version="${1:-}"
  local version_file="${LEROBOT_DIR}/.lerobot-version"
  local pyproject_file="${LEROBOT_DIR}/pyproject.toml"
  local temporary_file

  if [[ "${version}" == "-h" || "${version}" == "--help" ]]; then
    usage
    return 0
  fi
  [[ -n "${version}" && $# -eq 1 ]] || {
    usage >&2
    return 2
  }

  require_command uv

  temporary_file="$(mktemp)"
  sed -E "s/^(  \"lerobot\[[^]]*\])==[^\"]+(\",)\$/\1==${version}\2/" \
    "${pyproject_file}" >"${temporary_file}"
  if diff -q "${pyproject_file}" "${temporary_file}" &>/dev/null; then
    rm -f "${temporary_file}"
    die "no lerobot dependency line matched in ${pyproject_file}"
  fi
  mv "${temporary_file}" "${pyproject_file}"

  temporary_file="$(mktemp)"
  sed "s|^LEROBOT_VERSION=.*|LEROBOT_VERSION=${version}|" "${version_file}" >"${temporary_file}"
  mv "${temporary_file}" "${version_file}"

  (cd "${LEROBOT_DIR}" && uv lock)

  printf 'Updated LeRobot to %s\n' "${version}"
  git -C "${LEROBOT_REPO_ROOT}" status --short \
    "${LEROBOT_DIR#"${LEROBOT_REPO_ROOT}/"}/.lerobot-version" \
    "${LEROBOT_DIR#"${LEROBOT_REPO_ROOT}/"}/pyproject.toml" \
    "${LEROBOT_DIR#"${LEROBOT_REPO_ROOT}/"}/uv.lock"
}

main "$@"
