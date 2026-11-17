#!/usr/bin/env bash
# Evaluate datasets/leisaac-pick-orange with the VLM judge.
# Tiles the front + wrist cameras horizontally into composite frames.
set -o errexit -o nounset
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

exec "$SCRIPT_DIR/evaluate-dataset.sh" \
  --dataset "$REPO_ROOT/datasets/leisaac-pick-orange" \
  --views observation.images.front observation.images.wrist \
  "$@"
