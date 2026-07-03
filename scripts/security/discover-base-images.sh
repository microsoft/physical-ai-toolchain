#!/usr/bin/env bash
# cspell:ignore RSTART RLENGTH
# Print the unique, digest-pinned external base images referenced by FROM lines
# across every Dockerfile in the repo (one per line). Stage aliases and
# ARG/scratch bases carry no @sha256 digest and are excluded. Shared by
# container-scan.yml and container-cve-remediation.sh so both discover the same
# image set from a single source of truth.
set -o errexit -o nounset -o pipefail

dockerfile_list="$(mktemp)"
trap 'rm -f "$dockerfile_list"' EXIT

git ls-files -z '*Dockerfile*' > "$dockerfile_list"

dockerfiles=()
while IFS= read -r -d '' file; do
  dockerfiles+=("$file")
done < "$dockerfile_list"

if [[ "${#dockerfiles[@]}" -eq 0 ]]; then
  exit 0
fi

awk '
  /^[[:space:]]*[Ff][Rr][Oo][Mm][[:space:]]/ {
    line = $0
    while (match(line, /([A-Za-z0-9.-]+(:[0-9]+)?\/)?[A-Za-z0-9._\/-]+(:[A-Za-z0-9._-]+)?@sha256:[0-9A-Fa-f]{64}/)) {
      print substr(line, RSTART, RLENGTH)
      line = substr(line, RSTART + RLENGTH)
    }
  }
' "${dockerfiles[@]}" | sort -u
