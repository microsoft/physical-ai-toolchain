#!/usr/bin/env bash
# Evaluate datasets/cnc_lerobot with the VLM judge.
set -o errexit -o nounset
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

exec "$SCRIPT_DIR/evaluate-dataset.sh" \
  --dataset "$REPO_ROOT/datasets/cnc_lerobot" \
  --instruction "Operate the CNC machine: load the part, run the program, and unload the finished part." \
  --views observation.images.color \
  "$@"
