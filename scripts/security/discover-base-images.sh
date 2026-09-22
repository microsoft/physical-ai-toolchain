#!/usr/bin/env bash
# Print the unique, digest-pinned external base images referenced by FROM lines
# across every Dockerfile/Containerfile in the repo (one per line). Stage
# aliases and ARG/scratch bases carry no @sha256 digest and are excluded.
# Consumed by container-scan.yml to build the per-image scan matrix.
set -o errexit -o nounset -o pipefail

dockerfile_list="$(mktemp)"
trap 'rm -f "$dockerfile_list"' EXIT

git ls-files -z '*Dockerfile*' '*Containerfile*' > "$dockerfile_list"

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
# under pipefail (grep exits 1 on no match). The tag portion of the pattern
# also allows '$', '{', '}' so an ARG-templated tag (e.g.
# 'python:${PYTHON_VERSION}-slim@sha256:...') is captured whole rather than
# truncated mid-match; the final grep -v then drops any such unresolved
# template, since it isn't a concrete, statically pullable image reference.
printf '%s\0' "${dockerfiles[@]}" \
  | xargs -0 grep -hiE '^[[:space:]]*FROM[[:space:]]' \
  | grep -oiE '([A-Za-z0-9.-]+(:[0-9]+)?/)?[A-Za-z0-9._/-]+(:[A-Za-z0-9._${}-]+)?@sha256:[0-9a-f]{64}' \
  | grep -v '[${}]' \
  | sort -u || true
