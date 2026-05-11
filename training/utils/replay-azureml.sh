#!/usr/bin/env bash
# Replay a completed OSMO training run to Azure ML.
#
# Usage: ./replay-azureml.sh <run-id> [model-name]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

run_id="${1:?Usage: $0 <run-id> [model-name]}"
model_name="${2:-${AZUREML_MODEL_NAME:-lerobot-policy}}"

require_tools osmo

section "Submit Azure ML Replay"
print_kv "Run ID"     "$run_id"
print_kv "Model name" "$model_name"

osmo workflow submit "$REPO_ROOT/workflows/osmo/replay-azureml.yaml" \
  --set-string "run_id=$run_id" \
  --set-string "model_name=$model_name"

section "Deployment Summary"
print_kv "Status" "submitted"
