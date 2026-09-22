#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Build or publish the pinned LeRobot image with Docker Buildx.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: build_container.sh [OPTIONS]

Options:
  --image IMAGE     Image name (default: lerobot-so101)
  --tag TAG         Image tag; repeatable (default: pinned LeRobot version)
  --platforms LIST  Build platforms (default: host, or amd64+arm64 with --push)
  --push            Push a multi-platform image manifest
  -h, --help        Show this help

Examples:
  build_container.sh --tag dev
  build_container.sh --push --image ghcr.io/org/lerobot-so101
EOF
}

host_platform() {
  case "$(uname -m)" in
    x86_64)
      printf 'linux/amd64\n'
      ;;
    aarch64|arm64)
      printf 'linux/arm64\n'
      ;;
    *)
      die "unsupported host architecture: $(uname -m)"
      ;;
  esac
}

validate_builder_platforms() {
  local requested_platforms="$1"
  local supported_platforms

  supported_platforms="$(
    docker buildx inspect --bootstrap | sed -n 's/^Platforms: //p'
  )"
  if [[ "${requested_platforms}" == *linux/arm64* && \
    "${supported_platforms}" != *linux/arm64* ]]; then
    die "the active Buildx builder does not support linux/arm64; configure a docker-container builder with arm64 emulation"
  fi
}

main() {
  local image=""
  local platforms=""
  local push=false
  local output_mode=--load
  local tag
  local -a tags=()
  local -a tag_args=()

  while (( $# > 0 )); do
    case "$1" in
      --image)
        require_value "$1" "${2:-}"
        image="$2"
        shift 2
        ;;
      --tag|-t)
        require_value "$1" "${2:-}"
        tags+=("$2")
        shift 2
        ;;
      --platforms)
        require_value "$1" "${2:-}"
        platforms="$2"
        shift 2
        ;;
      --push|-p)
        push=true
        output_mode=--push
        shift
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done

  require_command docker
  docker buildx version &>/dev/null || die "Docker Buildx is required"

  if [[ -z "${image}" ]]; then
    image="lerobot-so101"
  fi
  if (( ${#tags[@]} == 0 )); then
    tags=("$(pinned_version)")
  fi
  if [[ -z "${platforms}" ]]; then
    if [[ "${push}" == true ]]; then
      platforms="linux/amd64,linux/arm64"
    else
      platforms="$(host_platform)"
    fi
  fi
  if [[ "${platforms}" == *,* && "${push}" != true ]]; then
    die "multi-platform builds require --push because Docker cannot --load a manifest list"
  fi
  validate_builder_platforms "${platforms}"

  for tag in "${tags[@]}"; do
    tag_args+=(--tag "${image}:${tag}")
  done

  docker buildx build \
    "${output_mode}" \
    --platform "${platforms}" \
    --build-context "runtime=${GPU_OFFLOAD_DIR}/runtime" \
    "${tag_args[@]}" \
    --file "${LEROBOT_DIR}/docker/Dockerfile" \
    "${LEROBOT_DIR}"
}

main "$@"