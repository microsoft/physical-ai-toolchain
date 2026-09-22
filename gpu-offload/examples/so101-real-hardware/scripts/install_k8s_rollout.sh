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
  local offload=""
  local has_values=false
  local -a helm_args=()
  local -a image_args=()
  local -a offload_args=()
  local -a values_args=()

  while (( $# > 0 )); do
    case "$1" in
      --offload)
        offload=true
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

  local job_name="${release}-lerobot-rollout"
  if kubectl get job -n "${namespace}" "${job_name}" &>/dev/null; then
    local active suspended
    active="$(kubectl get job -n "${namespace}" "${job_name}" -o jsonpath='{.status.active}')"
    suspended="$(kubectl get job -n "${namespace}" "${job_name}" -o jsonpath='{.spec.suspend}')"
    if [[ -n "${active}" && "${active}" != "0" && "${suspended}" != "true" ]]; then
      die "refusing to replace active Job ${namespace}/${job_name}"
    fi
    kubectl delete job -n "${namespace}" "${job_name}" --wait=true
  fi

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
    --set job.suspend=true \
    "${offload_args[@]}" \
    "${image_args[@]}" \
    "${helm_args[@]}"

  kubectl get job -n "${namespace}" "${job_name}"
}

main "$@"
