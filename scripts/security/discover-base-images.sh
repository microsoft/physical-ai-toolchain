#!/usr/bin/env bash
# Print the unique, digest-pinned external base images referenced by FROM lines
# across every Dockerfile in the repo (one per line). Stage aliases and
# ARG/scratch bases carry no @sha256 digest and are excluded. Consumed by
# container-scan.yml to build the per-image scan matrix.
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

# Extract digest-pinned base refs from FROM lines. grep -E is used rather than
# awk because awk interval expressions ({64}) are unsupported by the default
# mawk on Debian/Ubuntu (including the devcontainer), where the parser would
# silently emit nothing. || true keeps the "no digest-pinned bases" exit 0
# under pipefail (grep exits 1 on no match).
printf '%s\0' "${dockerfiles[@]}" \
  | xargs -0 grep -hiE '^[[:space:]]*FROM[[:space:]]' \
  | grep -oiE '([A-Za-z0-9.-]+(:[0-9]+)?/)?[A-Za-z0-9._/-]+(:[A-Za-z0-9._-]+)?@sha256:[0-9a-f]{64}' \
  | sort -u || true
