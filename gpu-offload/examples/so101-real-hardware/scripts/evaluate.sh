#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Evaluate a policy in a LeRobot simulation environment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: evaluate.sh --policy PATH --env-type TYPE [OPTIONS] [-- OVERRIDES]

Options:
  --policy PATH       Local or Hugging Face policy path (required)
  --env-type TYPE     LeRobot environment type, such as pusht (required)
  --episodes N        Evaluation episodes (default: 10)
  --output DIR        Evaluation output directory
  -h, --help          Show this help

Arguments after -- are passed directly to lerobot-eval. Real-robot runs use
rollout.sh because lerobot-eval evaluates simulation environments.
EOF
}

main() {
  local policy=""
  local env_type=""
  local episodes=10
  local output=""
  local -a overrides=()

  while (( $# > 0 )); do
    case "$1" in
      --policy)
        require_value "$1" "${2:-}"
        policy="$2"
        shift 2
        ;;
      --env-type)
        require_value "$1" "${2:-}"
        env_type="$2"
        shift 2
        ;;
      --episodes)
        require_value "$1" "${2:-}"
        episodes="$2"
        shift 2
        ;;
      --output)
        require_value "$1" "${2:-}"
        output="$2"
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

  [[ -n "${policy}" ]] || die "--policy is required"
  [[ -n "${env_type}" ]] || die "--env-type is required"
  require_positive_integer "--episodes" "${episodes}"
  require_command lerobot-eval

  local -a output_args=()
  if [[ -n "${output}" ]]; then
    output_args=("--output_dir=${output}")
  fi

  local -a command=(
    lerobot-eval
    "--policy.path=${policy}"
    "--env.type=${env_type}"
    "--eval.n_episodes=${episodes}"
    "${output_args[@]}"
    "${overrides[@]}"
  )

  "${command[@]}"
}

main "$@"
