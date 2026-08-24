#!/usr/bin/env bash
# Replay a completed OSMO training run to Azure ML.
#
# Usage: ./replay-azureml.sh <run-id> [model-name]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

run_id="${1:?Usage: $0 <run-id> [model-name]}"
model_name="${2:-${AZUREML_MODEL_NAME:-lerobot-policy}}"

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

require_tools osmo uv

aml_mirror_script="$SCRIPT_DIR/aml_mirror.py"
if [[ ! -f "$aml_mirror_script" ]]; then
  fatal "aml_mirror.py not found at $aml_mirror_script"
fi

osmo_project="$REPO_ROOT/workflows/osmo"
if [[ ! -f "$osmo_project/uv.lock" ]]; then
  fatal "uv.lock not found at $osmo_project/uv.lock"
fi

section "Submit Azure ML Replay"
print_kv "Run ID"     "$run_id"
print_kv "Model name" "$model_name"

aml_mirror_b64=$(base64 < "$aml_mirror_script" | tr -d '\n')
# Derive the AML mirror runtime requirements from the committed lock at submit
# time so there is no committed flat file to drift; --frozen guarantees the
# lock is read, not regenerated.
aml_mirror_requirements_b64=$(uv export --frozen --no-hashes --no-emit-project \
  --project "$osmo_project" | base64 | tr -d '\n')

submit_args=(
  workflow submit "$REPO_ROOT/workflows/osmo/replay-azureml.yaml"
  --set-string "run_id=$run_id"
  "model_name=$model_name"
  "aml_mirror_b64=$aml_mirror_b64"
  "aml_mirror_requirements_b64=$aml_mirror_requirements_b64"
)

[[ -n "$subscription_id" ]] && submit_args+=("azure_subscription_id=$subscription_id")
[[ -n "$resource_group" ]]  && submit_args+=("azure_resource_group=$resource_group")
[[ -n "$workspace_name" ]]  && submit_args+=("azureml_workspace_name=$workspace_name")

osmo "${submit_args[@]}"

section "Deployment Summary"
print_kv "Status" "submitted"
