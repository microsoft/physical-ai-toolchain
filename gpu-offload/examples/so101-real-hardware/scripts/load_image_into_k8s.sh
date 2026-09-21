#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

main() {
  local image="${IMAGE:-lerobot-so101:$(pinned_version)}"

  require_command docker
  require_command sudo
  docker image inspect "${image}" &>/dev/null || die "Docker image not found: ${image}"

  docker save "${image}" | sudo ctr --namespace k8s.io images import -
  sudo ctr --namespace k8s.io images list | grep -F "${image}"
}

main "$@"
