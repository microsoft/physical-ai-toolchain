#!/usr/bin/env bash
# Poll AzureML model registry for new checkpoints and submit OSMO inference per version
# Runs until the training workflow reaches a terminal state or is interrupted
set -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

source "$REPO_ROOT/deploy/002-setup/lib/common.sh"
source "$SCRIPT_DIR/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/deploy/001-iac" 2>/dev/null || true

ENV_FILE="${SCRIPT_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: poll-and-eval-checkpoints.sh [OPTIONS]

Poll AzureML model registry for new checkpoints of a training run and
submit an OSMO inference job for each new version as it appears.

REQUIRED:
    --model-name NAME             AzureML model registry name to watch
    --training-workflow-id ID     OSMO workflow ID of the training job
    --blob-prefix PREFIX          Blob path prefix for the evaluation dataset

OPTIONS:
    --storage-account NAME        Azure Storage account (default: from .env/Terraform)
    --eval-episodes N             Episodes per inference run (default: 10)
    --job-prefix NAME             Prefix for inference job names (default: derived from model)
    --experiment-name NAME        MLflow experiment for inference runs
    --poll-interval N             Seconds between registry polls (default: 60)
    --max-concurrent N            Max simultaneous inference workflows (default: 2)
    --azure-resource-group NAME   Azure resource group (default: from .env)
    --azure-workspace-name NAME   AzureML workspace name (default: from .env)
    -h, --help                    Show this help message

EXAMPLES:
    poll-and-eval-checkpoints.sh \
      --model-name hexagon-act-model-0304 \
      --training-workflow-id lerobot-training-32 \
      --blob-prefix hexagon_lerobot \
      --job-prefix hexagon-act-eval-0304 \
      --experiment-name hexagon-act-inference-0304
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

model_name=""
training_workflow_id=""
blob_prefix=""
storage_account="${AZURE_STORAGE_ACCOUNT_NAME:-}"
eval_episodes=10
job_prefix=""
experiment_name=""
poll_interval=60
max_concurrent=2
resource_group="${AZURE_RESOURCE_GROUP:-}"
workspace_name="${AZUREML_WORKSPACE_NAME:-}"

#------------------------------------------------------------------------------
# Argument parsing
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-name)          model_name="$2";           shift 2 ;;
    --training-workflow-id) training_workflow_id="$2"; shift 2 ;;
    --blob-prefix)         blob_prefix="$2";          shift 2 ;;
    --storage-account)     storage_account="$2";      shift 2 ;;
    --eval-episodes)       eval_episodes="$2";        shift 2 ;;
    --job-prefix)          job_prefix="$2";           shift 2 ;;
    --experiment-name)     experiment_name="$2";      shift 2 ;;
    --poll-interval)       poll_interval="$2";        shift 2 ;;
    --max-concurrent)      max_concurrent="$2";       shift 2 ;;
    --azure-resource-group) resource_group="$2";      shift 2 ;;
    --azure-workspace-name) workspace_name="$2";      shift 2 ;;
    -h|--help)             show_help; exit 0 ;;
    *) error "Unknown option: $1"; show_help; exit 1 ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

[[ -z "$model_name" ]]            && error "--model-name is required"            && exit 1
[[ -z "$training_workflow_id" ]]  && error "--training-workflow-id is required"  && exit 1
[[ -z "$blob_prefix" ]]           && error "--blob-prefix is required"           && exit 1
[[ -z "$storage_account" ]]       && error "--storage-account is required (set AZURE_STORAGE_ACCOUNT_NAME or pass --storage-account)" && exit 1
[[ -z "$resource_group" ]]        && error "--azure-resource-group is required"  && exit 1
[[ -z "$workspace_name" ]]        && error "--azure-workspace-name is required"  && exit 1

if [[ -z "$job_prefix" ]]; then
  job_prefix="${model_name}-eval"
fi

if [[ -z "$experiment_name" ]]; then
  experiment_name="${model_name}-inference"
fi

#------------------------------------------------------------------------------
# Helper: get current known model versions from AzureML
#------------------------------------------------------------------------------

get_known_versions() {
  az ml model list \
    --name "$model_name" \
    --resource-group "$resource_group" \
    --workspace-name "$workspace_name" \
    --query "[].version" \
    --output tsv 2>/dev/null | sort -n || true
}

#------------------------------------------------------------------------------
# Helper: check OSMO workflow terminal state
#------------------------------------------------------------------------------

# Returns 0 if workflow is in a terminal state (completed/failed/cancelled)
is_training_terminal() {
  local status
  status=$(osmo workflow query "$training_workflow_id" 2>/dev/null \
    | grep -Ei 'status|state' | grep -Eo 'completed|failed|failed_canceled|cancelled' | head -1 || true)
  [[ -n "$status" ]]
}

#------------------------------------------------------------------------------
# Helper: count active OSMO inference workflows for this run
#------------------------------------------------------------------------------

count_active_inference_jobs() {
  osmo workflow list 2>/dev/null \
    | grep -c "${job_prefix}" || true
}

#------------------------------------------------------------------------------
# Helper: submit inference for a single model version
#------------------------------------------------------------------------------

submit_inference() {
  local version="$1"
  local job_name="${job_prefix}-v${version}"

  info "Submitting inference for ${model_name} v${version} → job: ${job_name}"

  "$SCRIPT_DIR/submit-osmo-lerobot-inference.sh" \
    --from-aml-model \
    --model-name "$model_name" \
    --model-version "$version" \
    --from-blob-dataset \
    --storage-account "$storage_account" \
    --blob-prefix "$blob_prefix" \
    --eval-episodes "$eval_episodes" \
    --mlflow-enable \
    -j "$job_name" \
    --experiment-name "$experiment_name" \
    2>&1 | tee -a "/tmp/${model_name}-eval.log"
}

#------------------------------------------------------------------------------
# Main polling loop
#------------------------------------------------------------------------------

section "Checkpoint Poll-and-Eval"
print_kv "Model"              "$model_name"
print_kv "Training Workflow"  "$training_workflow_id"
print_kv "Dataset Blob"       "${storage_account}/datasets/${blob_prefix}"
print_kv "Eval Episodes"      "$eval_episodes"
print_kv "Job Prefix"         "$job_prefix"
print_kv "Experiment"         "$experiment_name"
print_kv "Poll Interval"      "${poll_interval}s"
print_kv "Max Concurrent"     "$max_concurrent"
echo ""

# Track submitted versions in a temp file (bash 3.2 compatible — no associative arrays)
submitted_file="/tmp/${model_name}-submitted-versions.txt"
: > "$submitted_file"
rounds=0

is_version_submitted() {
  grep -qxF "$1" "$submitted_file" 2>/dev/null
}

mark_version_submitted() {
  echo "$1" >> "$submitted_file"
}

while true; do
  rounds=$((rounds + 1))

  # Collect all currently registered versions
  all_versions=( $(get_known_versions) )

  # Identify new versions not yet submitted for inference
  new_versions=()
  for v in "${all_versions[@]+"${all_versions[@]}"}"; do
    if ! is_version_submitted "$v"; then
      new_versions+=("$v")
    fi
  done

  if [[ ${#new_versions[@]} -gt 0 ]]; then
    info "[round ${rounds}] Found ${#new_versions[@]} new checkpoint(s): ${new_versions[*]}"

    for v in "${new_versions[@]}"; do
      # Respect max-concurrent limit
      while true; do
        active=$(count_active_inference_jobs)
        if [[ "$active" -lt "$max_concurrent" ]]; then
          break
        fi
        info "  ${active} inference jobs active (max ${max_concurrent}), waiting 30s..."
        sleep 30
      done

      submit_inference "$v"
      mark_version_submitted "$v"
    done
  else
    info "[round ${rounds}] No new checkpoints. Known versions: ${#all_versions[@]} total."
  fi

  # Exit once training has reached a terminal state and all versions are submitted
  if is_training_terminal; then
    # One final poll to catch any late-registered checkpoints
    final_versions=( $(get_known_versions) )
    missed=()
    for v in "${final_versions[@]+"${final_versions[@]}"}"; do
      is_version_submitted "$v" || missed+=("$v")
    done
    if [[ ${#missed[@]} -gt 0 ]]; then
      info "Training finished. Submitting ${#missed[@]} remaining checkpoint(s): ${missed[*]}"
      for v in "${missed[@]}"; do
        submit_inference "$v"
        mark_version_submitted "$v"
      done
    fi
    info "Training workflow ${training_workflow_id} reached terminal state. Polling complete."
    break
  fi

  sleep "$poll_interval"
done

total_submitted=$(wc -l < "$submitted_file" | tr -d ' ')
section "Deployment Summary"
print_kv "Total Checkpoints Evaluated" "$total_submitted"
print_kv "Submitted Versions Log"      "$submitted_file"
print_kv "Inference Log"               "/tmp/${model_name}-eval.log"
