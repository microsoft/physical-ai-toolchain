#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Update the pinned LeRobot submodule to an explicit tag, branch, or commit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: update_upstream.sh REF

Update upstream/ to an explicit LeRobot tag, branch, or commit and record REF
in .lerobot-version. Review and commit both the version file and submodule
gitlink after validation.
EOF
}

main() {
  local ref="${1:-}"
  local version_file="${LEROBOT_DIR}/.lerobot-version"
  local upstream_dir="${LEROBOT_DIR}/upstream"
  local repository
  local commit
  local temporary_file

  if [[ "${ref}" == "-h" || "${ref}" == "--help" ]]; then
    usage
    return 0
  fi
  [[ -n "${ref}" && $# -eq 1 ]] || {
    usage >&2
    return 2
  }

  require_command git
  repository="$(sed -n 's/^LEROBOT_REPO=//p' "${version_file}")"
  [[ -n "${repository}" ]] || die "LEROBOT_REPO is missing from ${version_file}"

  local submodule_path="${LEROBOT_DIR#"${LEROBOT_REPO_ROOT}/"}/upstream"

  git -C "${LEROBOT_REPO_ROOT}" submodule update --init --recursive "${submodule_path}"
  if [[ -n "$(git -C "${upstream_dir}" status --short)" ]]; then
    die "upstream submodule has uncommitted changes"
  fi

  git -C "${upstream_dir}" remote set-url origin "${repository}"
  git -C "${upstream_dir}" fetch --tags origin "${ref}"
  commit="$(git -C "${upstream_dir}" rev-parse 'FETCH_HEAD^{commit}')"
  git -C "${upstream_dir}" checkout --detach "${commit}"

  temporary_file="$(mktemp)"
  sed "s|^LEROBOT_REF=.*|LEROBOT_REF=${ref}|" "${version_file}" >"${temporary_file}"
  mv "${temporary_file}" "${version_file}"

  printf 'Updated LeRobot to %s (%s)\n' "${ref}" "${commit}"
  git -C "${LEROBOT_REPO_ROOT}" status --short \
    "${LEROBOT_DIR#"${LEROBOT_REPO_ROOT}/"}/.lerobot-version" \
    "${submodule_path}"
}

main "$@"
