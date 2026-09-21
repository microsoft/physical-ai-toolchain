#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Run a trained policy on an SO-101 follower arm.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: rollout.sh --policy PATH [OPTIONS] [-- OVERRIDES]

Options:
  --policy PATH       Local or Hugging Face policy path (required)
  --task TASK         Task prompt for the policy
  --episodes N        Number of rollout sessions (default: 1)
  --duration S        Seconds per session (default: 60)
  --inference TYPE    sync or rtc (default: sync)
  --offload           Require execution through a Xavier-mutated workload
  -h, --help          Show this help

Arguments after -- are passed directly to lerobot-rollout.
EOF
}

main() {
  local policy=""
  local task=""
  local episodes=1
  local duration=60
  local inference=sync
  local offload=false
  local robot_cameras="${ROBOT_CAMERAS:-}"
  local episode
  local -a overrides=()

  while (( $# > 0 )); do
    case "$1" in
      --policy)
        require_value "$1" "${2:-}"
        policy="$2"
        shift 2
        ;;
      --task)
        require_value "$1" "${2:-}"
        task="$2"
        shift 2
        ;;
      --episodes)
        require_value "$1" "${2:-}"
        episodes="$2"
        shift 2
        ;;
      --duration)
        require_value "$1" "${2:-}"
        duration="$2"
        shift 2
        ;;
      --inference)
        require_value "$1" "${2:-}"
        inference="$2"
        shift 2
        ;;
      --offload)
        offload=true
        shift
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
  require_positive_integer "--episodes" "${episodes}"
  if [[ "${inference}" != "sync" && "${inference}" != "rtc" ]]; then
    die "--inference must be 'sync' or 'rtc'"
  fi
  if [[ "${offload}" == true && "${XAVIER_CONTAINER:-false}" != "true" ]]; then
    die "--offload requires a Xavier-mutated Kubernetes workload; use install_k8s_rollout.sh --offload"
  fi
  if [[ -z "${robot_cameras}" ]]; then
    robot_cameras="{}"
  fi
  require_command lerobot-rollout

  local -a command=(
    lerobot-rollout
    --strategy.type=base
    "--policy.path=${policy}"
    "--inference.type=${inference}"
    "--robot.type=${ROBOT_TYPE:-so101_follower}"
    "--robot.port=${ROBOT_PORT:-/dev/ttyACM0}"
    "--robot.id=${ROBOT_ID:-so101_follower}"
    "--robot.cameras=${robot_cameras}"
    "--fps=${LEROBOT_FPS:-30}"
    "--duration=${duration}"
    "--task=${task}"
    "${overrides[@]}"
  )

  if [[ "${ROLLOUT_TIMING_ENABLED:-false}" == "true" ]]; then
    command=(
      python
      /opt/xavier/lerobot/diagnostics/rollout_timing.py
      "${command[@]:1}"
    )
  elif [[ "${ROLLOUT_RAW_OBSERVATION_OFFLOAD:-false}" == "true" ]]; then
    command=(
      python
      /opt/xavier/lerobot/diagnostics/raw_observation_inference.py
      "${command[@]:1}"
    )
  fi

  for (( episode = 1; episode <= episodes; episode++ )); do
    printf 'Starting rollout session %d of %d\n' "${episode}" "${episodes}"
    "${command[@]}"
  done
}

main "$@"
