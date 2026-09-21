#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Train or finetune a LeRobot policy on a recorded dataset.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: finetune.sh --dataset ID --policy TYPE [OPTIONS] [-- OVERRIDES]

Options:
  --dataset ID         Hugging Face dataset ID (required)
  --policy TYPE        Policy type, such as act or smolvla (required)
  --steps N            Training steps (default: 100000)
  --output DIR         Output directory (default: outputs/train/<policy>_so101)
  --policy-path PATH   Start from a local or Hub pretrained policy
  -h, --help           Show this help

Arguments after -- are passed directly to lerobot-train.
EOF
}

main() {
  local dataset=""
  local policy_type=""
  local policy_path=""
  local steps=100000
  local output=""
  local -a overrides=()

  while (( $# > 0 )); do
    case "$1" in
      --dataset)
        require_value "$1" "${2:-}"
        dataset="$2"
        shift 2
        ;;
      --policy)
        require_value "$1" "${2:-}"
        policy_type="$2"
        shift 2
        ;;
      --steps)
        require_value "$1" "${2:-}"
        steps="$2"
        shift 2
        ;;
      --output)
        require_value "$1" "${2:-}"
        output="$2"
        shift 2
        ;;
      --policy-path)
        require_value "$1" "${2:-}"
        policy_path="$2"
        shift 2
        ;;
      --)
        shift
        overrides=("$@")
        break
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

  [[ -n "${dataset}" ]] || die "--dataset is required"
  [[ -n "${policy_type}" ]] || die "--policy is required"
  require_positive_integer "--steps" "${steps}"
  require_command lerobot-train

  if [[ -z "${output}" ]]; then
    output="outputs/train/${policy_type}_so101"
  fi

  local -a policy_args=("--policy.type=${policy_type}")
  if [[ -n "${policy_path}" ]]; then
    policy_args=("--policy.path=${policy_path}")
  fi

  local -a command=(
    lerobot-train
    "${policy_args[@]}"
    "--dataset.repo_id=${dataset}"
    "--steps=${steps}"
    "--output_dir=${output}"
    "${overrides[@]}"
  )

  "${command[@]}"
}

main "$@"
