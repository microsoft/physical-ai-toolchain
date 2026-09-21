#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

main() {
  local release="${RELEASE:-so101}"
  local namespace="${NAMESPACE:-lerobot}"
  local duration="${DURATION:-60}"
  local offload=""
  local auto_confirm=false
  local has_values=false
  local -a helm_args=()
  local -a image_args=()
  local -a offload_args=()
  local -a values_args=()
  local answer

  while (( $# > 0 )); do
    case "$1" in
      --offload)
        offload=true
        shift
        ;;
      --yes)
        auto_confirm=true
        shift
        ;;
      -f|--values)
        require_value "$1" "${2:-}"
        values_args+=("$1" "$2")
        has_values=true
        shift 2
        ;;
      *)
        helm_args+=("$1")
        shift
        ;;
    esac
  done

  require_command helm
  require_command kubectl

  if [[ "${auto_confirm}" != true ]]; then
    read -r -p "Kubernetes will move the SO-101 for ${duration}s. Is the workspace clear and emergency stop in reach? [y/N] " answer
    [[ "${answer}" =~ ^[Yy]$ ]] || die "rollout cancelled"
  fi

  kubectl delete job -n "${namespace}" "${release}-lerobot-rollout" --ignore-not-found
  if [[ "${has_values}" != true ]]; then
    image_args=(--set-string "image.tag=$(pinned_version)")
  fi
  if [[ -n "${offload}" ]]; then
    offload_args=(--set "offload.enabled=${offload}")
  fi

  helm upgrade --install "${release}" "${LEROBOT_DIR}/charts/lerobot-rollout" \
    --namespace "${namespace}" \
    --create-namespace \
    "${values_args[@]}" \
    --set job.suspend=false \
    "${offload_args[@]}" \
    "${image_args[@]}" \
    --set-string "rollout.duration=${duration}" \
    "${helm_args[@]}"

  kubectl logs -n "${namespace}" --follow "job/${release}-lerobot-rollout"
}

main "$@"
