#!/usr/bin/env bash
# Evaluate datasets/ur10e_episodes with the VLM judge.
set -o errexit -o nounset
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

exec "$SCRIPT_DIR/evaluate-dataset.sh" \
  --dataset "$REPO_ROOT/datasets/ur10e_episodes" \
  --views observation.images.color \
  "$@"
