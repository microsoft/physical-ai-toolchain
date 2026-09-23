#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# Discover concrete external base images and their stable scan lanes.
set -euo pipefail

show_help() {
  printf 'Usage: %s [--matrix | --config-preview | --help]\n' "${0##*/}"
}

main() {
  local mode=refs repo_root map_file files occurrences refs file line ordinal ref
  case "${1:-}" in
    '') ;;
    --matrix) mode=matrix ;;
    --config-preview) mode=preview ;;
    -h|--help) show_help; return 0 ;;
    *) show_help >&2; return 2 ;;
  esac
  if (( $# > 1 )); then
    show_help >&2
    return 2
  fi

  repo_root="$(git rev-parse --show-toplevel)" || return 1
  cd "${repo_root}"
  map_file="${repo_root}/scripts/security/container-scan-lanes.json"
  if [[ "${mode}" == preview ]]; then
    printf 'Mode: references\nMap: %s\n' "${map_file}" >&2
    return 0
  fi

  files="$(mktemp)"
  occurrences="$(mktemp)"
  refs="$(mktemp)"
  # Capture local paths before the function returns.
  # shellcheck disable=SC2064
  trap "rm -f -- $(printf '%q ' "${files}" "${occurrences}" "${refs}")" EXIT
  git ls-files -z '*Dockerfile*' '*Containerfile*' > "${files}"

  # The dollar sign is part of the template-detection regex.
  # shellcheck disable=SC2016
  local image_pattern='([A-Za-z0-9.-]+(:[0-9]+)?/)?[A-Za-z0-9._/-]+(:[A-Za-z0-9._${}-]+)?@sha256:[0-9a-f]{64}'
  shopt -s nocasematch
  while IFS= read -r -d '' file; do
    ordinal=0
    while IFS= read -r line || [[ -n "${line}" ]]; do
      if [[ "${line^^}" =~ ^[[:space:]]*FROM[[:space:]] ]]; then
        if [[ "${line}" =~ ${image_pattern} ]]; then
          ref="${BASH_REMATCH[0]}"
          if [[ "${ref}" != *'$'* && "${ref}" != *'{'* && "${ref}" != *'}'* ]]; then
            printf '%s\0%s\0%s\0' "${file}" "${ordinal}" "${ref}" >> "${occurrences}"
            printf '%s\n' "${ref}" >> "${refs}"
          fi
        fi
        ((ordinal += 1))
      fi
    done < "${file}"
  done < "${files}"

  if [[ "${mode}" == refs ]]; then
    sort -u "${refs}"
    return
  fi

  if [[ ! -f "${map_file}" ]]; then
    printf 'Missing scan lane map: %s\n' "${map_file}" >&2
    return 1
  fi
  # cspell:ignore slurpfile
  jq -cen --rawfile tracked "${files}" --rawfile occurrences "${occurrences}" \
    --slurpfile config "${map_file}" '
    def reject($message): error("Invalid scan lane map: " + $message);
    def keys_are($names): type == "object" and (keys == ($names | sort));
    def valid_path:
      type == "string" and length > 0 and
      (startswith("/") or test("(^|/)\\.\\.(/|$)|(^|/)\\.(/|$)|\\\\|^[A-Za-z]:") | not);
    ($tracked | split("\u0000") | map(select(length > 0))) as $tracked_paths |
    (if $occurrences == "" then [] else $occurrences | split("\u0000") | .[:-1] end) as $parts |
    if ($parts | length) % 3 != 0 then reject("corrupt source records") else . end |
    [range(0; ($parts | length); 3) |
      {path: $parts[.], from: ($parts[. + 1] | tonumber), image: $parts[. + 2]}] as $found |
    if ($config | length) != 1 or ($config[0] | keys_are(["lanes"]) | not)
      or ($config[0].lanes | type) != "array"
    then reject("expected a root object with lanes array") else $config[0].lanes end |
    . as $lanes |
    if any($lanes[];
      (keys_are(["id", "sources"]) | not)
      or (.id | type != "string" or (test("^[a-z][a-z0-9]*(-[a-z0-9]+)*$") | not) or (length > 80))
      or (.sources | type != "array" or length == 0)
    ) then reject("invalid lane ID, fields, or sources") else . end |
    if ([$lanes[].id] | unique | length) != ($lanes | length)
    then reject("duplicate lane ID") else . end |
    if any($lanes[].sources[]; (keys_are(["path", "from"]) | not)
      or (.path | valid_path | not)
      or (.path as $p | $tracked_paths | index($p) == null)
      or (.from | type != "number" or floor != . or . < 0))
    then reject("invalid or untracked source binding") else . end |
    [$lanes[] | .id as $lane | .sources[] | {lane: $lane, path, from}] as $bindings |
    if ([$bindings[] | [.path, .from] | @json] | unique | length) != ($bindings | length)
    then reject("duplicate source binding") else . end |
    [ $bindings[] as $b |
      [$found[] | select(.path == $b.path and .from == $b.from)] as $hits |
      if ($hits | length) != 1 then reject("missing or ineligible source slot: " + $b.path)
      else {lane: $b.lane, image: $hits[0].image} end
    ] as $resolved |
    if ([$found[] | [.path, .from] | @json] | sort) !=
      ([$bindings[] | [.path, .from] | @json] | sort)
    then reject("unmapped eligible source") else . end |
    if any($lanes[]; .id as $id |
      ([$resolved[] | select(.lane == $id) | .image] | unique | length) != 1)
    then reject("lane sources resolve to different references") else . end |
    if ([$resolved[].image] | unique | length) != ($lanes | length)
    then reject("one exact reference assigned to different lanes") else . end |
    [$lanes[] | .id as $id |
      {lane: $id, image: (first($resolved[] | select(.lane == $id) | .image)),
       category: ("trivy-image-" + $id)}] | sort_by(.lane)
  ' || return 1
}

main "$@"
