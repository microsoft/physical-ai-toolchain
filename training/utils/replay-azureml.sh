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

aml_mirror_script="$SCRIPT_DIR/aml_mirror.py"
if [[ ! -f "$aml_mirror_script" ]]; then
  fatal "aml_mirror.py not found at $aml_mirror_script"
fi

aml_mirror_requirements="$REPO_ROOT/workflows/osmo/requirements-aml-mirror.txt"
if [[ ! -f "$aml_mirror_requirements" ]]; then
  fatal "requirements-aml-mirror.txt not found at $aml_mirror_requirements"
fi

section "Submit Azure ML Replay"
print_kv "Run ID"     "$run_id"
print_kv "Model name" "$model_name"

aml_mirror_b64=$(base64 -w0 < "$aml_mirror_script")
aml_mirror_requirements_b64=$(base64 -w0 < "$aml_mirror_requirements")

osmo workflow submit "$REPO_ROOT/workflows/osmo/replay-azureml.yaml" \
  --set-string "run_id=$run_id" \
  --set-string "model_name=$model_name" \
  --set-string "aml_mirror_b64=$aml_mirror_b64" \
  --set-string "aml_mirror_requirements_b64=$aml_mirror_requirements_b64"

section "Deployment Summary"
print_kv "Status" "submitted"
