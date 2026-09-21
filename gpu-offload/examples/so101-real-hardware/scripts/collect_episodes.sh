#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Record an SO-101 dataset through leader-arm teleoperation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

usage() {
  cat <<'EOF'
Usage: collect_episodes.sh --repo-id ID --task TASK [OPTIONS] [-- OVERRIDES]

Options:
  --repo-id ID          Hugging Face dataset ID (required)
  --task TASK           Task description (required)
  --num-episodes N      Episodes to record (default: 50)
  --episode-time S      Seconds per episode (default: 60)
  --reset-time S        Seconds between episodes (default: 60)
  --no-push             Keep the dataset local
  -h, --help            Show this help

Arguments after -- are passed directly to lerobot-record.
EOF
}

main() {
  local repo_id=""
  local task=""
  local num_episodes=50
  local episode_time=60
  local reset_time=60
  local push_to_hub=true
  local -a overrides=()

  while (( $# > 0 )); do
    case "$1" in
      --repo-id)
        require_value "$1" "${2:-}"
        repo_id="$2"
        shift 2
        ;;
      --task)
        require_value "$1" "${2:-}"
        task="$2"
        shift 2
        ;;
      --num-episodes)
        require_value "$1" "${2:-}"
        num_episodes="$2"
        shift 2
        ;;
      --episode-time)
        require_value "$1" "${2:-}"
        episode_time="$2"
        shift 2
        ;;
      --reset-time)
        require_value "$1" "${2:-}"
        reset_time="$2"
        shift 2
        ;;
      --no-push)
        push_to_hub=false
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

  [[ -n "${repo_id}" ]] || die "--repo-id is required"
  [[ -n "${task}" ]] || die "--task is required"
  require_positive_integer "--num-episodes" "${num_episodes}"
  require_command lerobot-record

  local -a command=(
    lerobot-record
    "--robot.type=${ROBOT_TYPE:-so101_follower}"
    "--robot.port=${ROBOT_PORT:-/dev/ttyACM0}"
    "--robot.id=${ROBOT_ID:-so101_follower}"
    "--robot.cameras=${ROBOT_CAMERAS:-{}}"
    "--teleop.type=${TELEOP_TYPE:-so101_leader}"
    "--teleop.port=${TELEOP_PORT:-/dev/ttyACM1}"
    "--teleop.id=${TELEOP_ID:-so101_leader}"
    "--dataset.repo_id=${repo_id}"
    "--dataset.single_task=${task}"
    "--dataset.num_episodes=${num_episodes}"
    "--dataset.episode_time_s=${episode_time}"
    "--dataset.reset_time_s=${reset_time}"
    "--dataset.fps=${LEROBOT_FPS:-30}"
    "--dataset.push_to_hub=${push_to_hub}"
    --dataset.streaming_encoding=true
    --dataset.encoder_threads=2
    "${overrides[@]}"
  )

  "${command[@]}"
}

main "$@"