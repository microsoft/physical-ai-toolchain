#!/usr/bin/env bash
# Print the unique, digest-pinned external base images referenced by FROM lines
# across every Dockerfile/Containerfile in the repo (one per line). Stage
# aliases and ARG/scratch bases carry no @sha256 digest and are excluded.
# Consumed by container-scan.yml to build the per-image scan matrix.
set -o errexit -o nounset -o pipefail

dockerfiles=()
mapfile -d '' -t dockerfiles < <(git ls-files -z '*Dockerfile*' '*Containerfile*')
wait "$!"

if [[ "${#dockerfiles[@]}" -eq 0 ]]; then
  exit 0
fi

images=()
ref_pattern='^([A-Za-z0-9.-]+(:[0-9]+)?/)?[A-Za-z0-9._/-]+(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}$'
for file in "${dockerfiles[@]}"; do
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    read -r instruction image remainder <<< "$line"
    [[ "${instruction^^}" == FROM ]] || continue
    if [[ "$image" == --platform=* ]]; then
      read -r image remainder <<< "$remainder"
    fi
    if [[ "$image" =~ $ref_pattern ]]; then
      images+=("$image")
    fi
  done < "$file"
done

if [[ "${#images[@]}" -gt 0 ]]; then
  printf '%s\n' "${images[@]}" | sort -u
fi
