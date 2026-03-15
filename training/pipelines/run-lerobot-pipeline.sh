#!/usr/bin/env bash
# End-to-end LeRobot pipeline: train → wait → evaluate → register
# Orchestrates training and inference workflows with automatic polling
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

source "$REPO_ROOT/shared/lib/common.sh"
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

# Source .env file if present
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
Usage: run-lerobot-pipeline.sh [OPTIONS] [-- osmo-submit-flags]

End-to-end LeRobot pipeline: train → wait → evaluate → register.

Submits a training workflow, polls for completion, then submits an inference
workflow against the trained policy. Optionally registers the model to Azure ML.

REQUIRED:
    -d, --dataset-repo-id ID      HuggingFace dataset repository (e.g., user/dataset)
        --policy-repo-id ID       HuggingFace repo where trained policy will be pushed

TRAINING OPTIONS:
    -p, --policy-type TYPE        Policy architecture: act, diffusion (default: act)
    -j, --job-name NAME           Job identifier prefix (default: lerobot-pipeline)
    -i, --image IMAGE             Container image override
        --training-steps N        Total training iterations
        --batch-size N            Training batch size
        --save-freq N             Checkpoint save frequency (default: 5000)

LOGGING OPTIONS:
        --wandb-enable            Enable WANDB logging (default)
        --no-wandb                Disable WANDB logging
        --wandb-project NAME      WANDB project name (default: lerobot-training)
        --mlflow-enable           Enable Azure ML MLflow logging
        --experiment-name NAME    MLflow experiment name

INFERENCE OPTIONS:
        --eval-episodes N         Evaluation episodes (default: 10)
        --skip-inference          Skip the inference stage

REGISTRATION OPTIONS:
    -r, --register-model NAME     Register model to Azure ML after inference
        --skip-register           Skip model registration during training

PIPELINE OPTIONS:
        --poll-interval SECS      Status check interval (default: 60)
        --timeout MINS            Training timeout in minutes (default: 720)
        --skip-wait               Submit training without waiting (async mode)

AZURE CONTEXT:
        --azure-subscription-id ID    Azure subscription ID
        --azure-resource-group NAME   Azure resource group
        --azure-workspace-name NAME   Azure ML workspace

GENERAL:
        --use-local-osmo          Use local osmo-dev CLI instead of production osmo
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

EXAMPLES:
    # Full pipeline: train ACT → evaluate → register
    run-lerobot-pipeline.sh \
      -d lerobot/aloha_sim_insertion_human \
      --policy-repo-id user/my-act-policy \
      -r my-act-model

    # Train and evaluate without registration
    run-lerobot-pipeline.sh \
      -d user/my-dataset \
      --policy-repo-id user/my-policy

    # Async mode (submit training and exit)
    run-lerobot-pipeline.sh \
      -d user/my-dataset \
      --skip-wait

    # MLflow pipeline with custom training
    run-lerobot-pipeline.sh \
      -d user/my-dataset \
      --policy-repo-id user/my-policy \
      -p diffusion \
      --mlflow-enable \
      --training-steps 100000 \
      -r my-diffusion-model
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

dataset_repo_id="${DATASET_REPO_ID:-}"
policy_repo_id="${POLICY_REPO_ID:-}"
policy_type="${POLICY_TYPE:-act}"
job_name="${JOB_NAME:-lerobot-pipeline}"
image="${IMAGE:-}"

training_steps="${TRAINING_STEPS:-}"
batch_size="${BATCH_SIZE:-}"
save_freq="${SAVE_FREQ:-5000}"

wandb_enable="${WANDB_ENABLE:-true}"
wandb_project="${WANDB_PROJECT:-lerobot-training}"
mlflow_enable="${MLFLOW_ENABLE:-false}"
experiment_name="${EXPERIMENT_NAME:-}"

eval_episodes="${EVAL_EPISODES:-10}"
skip_inference=false

register_model="${REGISTER_MODEL:-}"
skip_register=false

poll_interval="${POLL_INTERVAL:-60}"
timeout_mins="${TIMEOUT_MINS:-720}"
skip_wait=false
use_local_osmo=false
config_preview=false

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"

forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    -i|--image)                   image="$2"; shift 2 ;;
    --training-steps)             training_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --save-freq)                  save_freq="$2"; shift 2 ;;
    --wandb-enable)               wandb_enable="true"; shift ;;
    --no-wandb)                   wandb_enable="false"; shift ;;
    --wandb-project)              wandb_project="$2"; shift 2 ;;
    --mlflow-enable)              mlflow_enable="true"; shift ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    --eval-episodes)              eval_episodes="$2"; shift 2 ;;
    --skip-inference)             skip_inference=true; shift ;;
    -r|--register-model)          register_model="$2"; shift 2 ;;
    --skip-register)              skip_register=true; shift ;;
    --poll-interval)              poll_interval="$2"; shift 2 ;;
    --timeout)                    timeout_mins="$2"; shift 2 ;;
    --skip-wait)                  skip_wait=true; shift ;;
    --use-local-osmo)             use_local_osmo=true; shift ;;
    --config-preview)             config_preview=true; shift ;;
    --azure-subscription-id)      subscription_id="$2"; shift 2 ;;
    --azure-resource-group)       resource_group="$2"; shift 2 ;;
    --azure-workspace-name)       workspace_name="$2"; shift 2 ;;
    --)                           shift; forward_args=("$@"); break ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

[[ "$use_local_osmo" == "true" ]] && activate_local_osmo

require_tools osmo jq

[[ -z "$dataset_repo_id" ]] && fatal "--dataset-repo-id is required"
if [[ "$skip_wait" == "false" && "$skip_inference" == "false" ]]; then
  [[ -z "$policy_repo_id" ]] && fatal "--policy-repo-id is required for the inference stage (or use --skip-inference)"
fi

case "$policy_type" in
  act|diffusion) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion)" ;;
esac

if [[ "$mlflow_enable" == "true" || -n "$register_model" ]]; then
  [[ -z "$subscription_id" ]] && fatal "Azure subscription ID required for MLflow/registration"
  [[ -z "$resource_group" ]] && fatal "Azure resource group required for MLflow/registration"
  [[ -z "$workspace_name" ]] && fatal "Azure ML workspace name required for MLflow/registration"
fi

train_job_name="${job_name}-train"
eval_job_name="${job_name}-eval"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Dataset" "$dataset_repo_id"
  print_kv "Policy Repo" "${policy_repo_id:-<not set>}"
  print_kv "Policy Type" "$policy_type"
  print_kv "Training Job" "${job_name}-train"
  print_kv "Inference Job" "${job_name}-eval"
  print_kv "Training Steps" "${training_steps:-<default>}"
  print_kv "Batch Size" "${batch_size:-<default>}"
  print_kv "Save Freq" "$save_freq"
  print_kv "Eval Episodes" "$eval_episodes"
  print_kv "WANDB" "$wandb_enable"
  print_kv "MLflow" "$mlflow_enable"
  print_kv "Skip Inference" "$skip_inference"
  print_kv "Skip Wait" "$skip_wait"
  print_kv "Poll Interval" "${poll_interval}s"
  print_kv "Timeout" "${timeout_mins}m"
  print_kv "Register Model" "${register_model:-<none>}"
  print_kv "Subscription" "${subscription_id:-<not set>}"
  print_kv "Resource Group" "${resource_group:-<not set>}"
  print_kv "Workspace" "${workspace_name:-<not set>}"
  exit 0
fi

#------------------------------------------------------------------------------
# Stage 1: Submit Training
#------------------------------------------------------------------------------

section "Stage 1: Training"
info "Submitting LeRobot training workflow..."

train_args=(
  "$REPO_ROOT/training/il/scripts/submit-osmo-lerobot-training.sh"
  --dataset-repo-id "$dataset_repo_id"
  --policy-type "$policy_type"
  --job-name "$train_job_name"
  --save-freq "$save_freq"
)

[[ -n "$image" ]]            && train_args+=(--image "$image")
[[ -n "$policy_repo_id" ]]   && train_args+=(--policy-repo-id "$policy_repo_id")
[[ -n "$training_steps" ]]   && train_args+=(--training-steps "$training_steps")
[[ -n "$batch_size" ]]       && train_args+=(--batch-size "$batch_size")
[[ -n "$experiment_name" ]]  && train_args+=(--experiment-name "$experiment_name")

if [[ "$wandb_enable" == "true" ]]; then
  train_args+=(--wandb-enable --wandb-project "$wandb_project")
else
  train_args+=(--no-wandb)
fi

[[ "$mlflow_enable" == "true" ]] && train_args+=(--mlflow-enable)

if [[ "$skip_register" == "false" && -n "$register_model" ]]; then
  train_args+=(--register-checkpoint "$register_model")
fi

if [[ -n "$subscription_id" ]]; then
  train_args+=(--azure-subscription-id "$subscription_id")
fi
if [[ -n "$resource_group" ]]; then
  train_args+=(--azure-resource-group "$resource_group")
fi
if [[ -n "$workspace_name" ]]; then
  train_args+=(--azure-workspace-name "$workspace_name")
fi

[[ "$use_local_osmo" == "true" ]] && train_args+=(--use-local-osmo)
[[ ${#forward_args[@]} -gt 0 ]] && train_args+=(-- "${forward_args[@]}")

bash "${train_args[@]}" || fatal "Training submission failed"

info "Training workflow submitted: $train_job_name"

if [[ "$skip_wait" == "true" ]]; then
  section "Deployment Summary"
  print_kv "Mode" "async (training submitted, not waiting)"
  print_kv "Training Job" "$train_job_name"
  print_kv "Monitor" "osmo workflow status $train_job_name"
  info "To continue the pipeline after training completes:"
  info "  $0 --skip-wait is not applicable for the remaining stages."
  info "  Instead, run inference separately:"
  info "  ./submit-osmo-lerobot-inference.sh --policy-repo-id $policy_repo_id"
  exit 0
fi

#------------------------------------------------------------------------------
# Stage 2: Wait for Training Completion
#------------------------------------------------------------------------------

section "Stage 2: Waiting for Training"
info "Polling workflow status every ${poll_interval}s (timeout: ${timeout_mins}m)..."

timeout_secs=$((timeout_mins * 60))
elapsed=0
workflow_status=""

while [[ $elapsed -lt $timeout_secs ]]; do
  # Capture workflow status from OSMO
  workflow_status=$(osmo workflow status "$train_job_name" --output json 2>/dev/null | \
    jq -r '.status // .state // empty' 2>/dev/null || echo "UNKNOWN")

  case "$workflow_status" in
    COMPLETED|completed|Completed|SUCCEEDED|succeeded)
      info "Training workflow completed successfully"
      break
      ;;
    FAILED|failed|Failed|ERROR|error)
      error "Training workflow failed"
      info "View logs: osmo workflow logs $train_job_name"
      fatal "Pipeline aborted due to training failure"
      ;;
    CANCELLED|cancelled|Canceled)
      fatal "Training workflow was cancelled"
      ;;
    *)
      # Still running or unknown state
      elapsed_mins=$((elapsed / 60))
      info "Status: $workflow_status | Elapsed: ${elapsed_mins}m / ${timeout_mins}m"
      sleep "$poll_interval"
      elapsed=$((elapsed + poll_interval))
      ;;
  esac
done

if [[ $elapsed -ge $timeout_secs ]]; then
  warn "Training timeout reached (${timeout_mins}m)"
  warn "Workflow may still be running. Check: osmo workflow status $train_job_name"
  fatal "Pipeline aborted due to timeout"
fi

#------------------------------------------------------------------------------
# Stage 3: Submit Inference
#------------------------------------------------------------------------------

if [[ "$skip_inference" == "true" ]]; then
  info "Skipping inference stage per --skip-inference"
else
  section "Stage 3: Inference"
  info "Submitting LeRobot inference workflow..."

  eval_args=(
    "$REPO_ROOT/scripts/submit-osmo-lerobot-inference.sh"
    --policy-repo-id "$policy_repo_id"
    --policy-type "$policy_type"
    --job-name "$eval_job_name"
    --eval-episodes "$eval_episodes"
  )

  [[ -n "$image" ]]   && eval_args+=(--image "$image")
  [[ -n "$dataset_repo_id" ]] && eval_args+=(--dataset-repo-id "$dataset_repo_id")

  if [[ -n "$register_model" ]]; then
    eval_args+=(--register-model "$register_model")
  fi

  if [[ -n "$subscription_id" ]]; then
    eval_args+=(--azure-subscription-id "$subscription_id")
  fi
  if [[ -n "$resource_group" ]]; then
    eval_args+=(--azure-resource-group "$resource_group")
  fi
  if [[ -n "$workspace_name" ]]; then
    eval_args+=(--azure-workspace-name "$workspace_name")
  fi

  [[ "$use_local_osmo" == "true" ]] && eval_args+=(--use-local-osmo)

  bash "${eval_args[@]}" || fatal "Inference submission failed"

  info "Inference workflow submitted: $eval_job_name"
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Deployment Summary"
print_kv "Dataset" "$dataset_repo_id"
print_kv "Policy Type" "$policy_type"
print_kv "Training Job" "$train_job_name"
[[ "$skip_inference" == "false" ]] && print_kv "Inference Job" "$eval_job_name"
[[ -n "$policy_repo_id" ]] && print_kv "Policy Repo" "$policy_repo_id"
[[ -n "$register_model" ]] && print_kv "Model Name" "$register_model"

info "Monitor workflows:"
info "  osmo workflow status $train_job_name"
[[ "$skip_inference" == "false" ]] && info "  osmo workflow status $eval_job_name"
