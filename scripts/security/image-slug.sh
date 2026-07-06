#!/usr/bin/env bash
# Print the disambiguation slug for a digest-pinned image ref
# (registry/repo:tag@sha256:...). A short hash of the full ref (digest included)
# is appended so two images whose tags collapse to the same alphanumeric slug
# still get distinct slugs. Used by container-scan.yml to name the per-image
# SARIF file and category.
set -o errexit -o nounset -o pipefail

ref="${1:?usage: image-slug.sh <image-ref>}"

hash="$(printf '%s' "$ref" | sha256sum | cut -c1-12)"
tag_slug="$(printf '%s' "${ref%@*}" | tr -c 'a-zA-Z0-9' '-' | sed 's/-\{2,\}/-/g; s/^-//; s/-$//')"
printf '%s-%s\n' "$tag_slug" "$hash"
