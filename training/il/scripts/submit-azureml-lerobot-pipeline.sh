#!/usr/bin/env bash
# Submit LeRobot E2E pipeline (preprocess -> train -> evaluate [-> register]) to AzureML
#
# This is the pipeline counterpart of submit-azureml-lerobot-training.sh. It
# submits an AzureML Pipeline job (jobs of type=pipeline) that chains:
#
#   preprocess -> train -> evaluate
#
# By default it uses training/il/workflows/azureml/lerobot-pipeline.yaml.
# With --with-register, it switches to lerobot-pipeline-with-register.yaml and
# adds an opt-in register step. The register step has no eval-gate enforcement;
# eval-gates-register governance is intentionally out of scope.
#
# Compared with submit-azureml-lerobot-training.sh:
#   - submission target is an AzureML Pipeline job, not a CommandJob
#   - per-step compute targets are set via pipeline.yaml inputs
#   - environment variables are scoped per step (--set jobs.STEP.env...)
#   - data asset multiplexing (1-64 assets) is not supported here; the
#     pipeline accepts a single uri_folder dataset input. Use the existing
#     training submit script for the multi-asset merge case.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../../scripts/lib/terraform-outputs.sh
source "$REPO_ROOT/scripts/lib/terraform-outputs.sh"
read_terraform_outputs "$REPO_ROOT/infrastructure/terraform" 2>/dev/null || true

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
Usage: submit-azureml-lerobot-pipeline.sh [OPTIONS] [-- az-ml-job-flags]

Submit an end-to-end LeRobot AzureML pipeline (preprocess -> train -> evaluate),
optionally followed by an opt-in model registration step.

REQUIRED:
    -d, --dataset-repo-id ID      Logical dataset repo id (folder naming;
                                  e.g. user/koch-pick-place-5-lego-random-pose)
        --dataset-asset URI       AzureML data asset URI for the raw dataset.
                                  Accepted forms:
                                    azureml:NAME:VERSION       (numeric version)
                                    azureml://.../data/NAME/versions/VERSION
                                  Shorthands like azureml:NAME or azureml:NAME@latest
                                  are rejected to keep runs reproducible.

PIPELINE OPTIONS:
        --preprocessing-config URI Optional preprocessing_config.yaml URI (uri_file)
                                  from a previous pipeline run to lock down
                                  preprocessing parameters across runs.
        --with-register           Use the 4-step pipeline that includes the
                                  opt-in register step.
        --register-model-name NAME AzureML model name for the register step
                                  (required when --with-register is set).

TRAINING OPTIONS (passed to train_step):
    -p, --policy-type TYPE        Policy architecture: act, diffusion, pi0
                                  (default: act)
    -j, --job-name NAME           Pipeline job identifier
                                  (default: lerobot-pipeline)
        --policy-repo-id ID       Pre-trained policy for fine-tuning (HuggingFace)
        --lerobot-version VER     Specific LeRobot version or "latest"
                                  (default: latest)
        --training-steps N        Total training iterations
        --batch-size N            Training batch size
        --eval-freq N             Train-loop evaluation frequency
        --save-freq N             Checkpoint save frequency (default: 5000)
        --mixed-precision MODE    Accelerate mixed-precision: no|fp16|bf16
                                  (default: no; multi-GPU only)

EVALUATION OPTIONS (passed to evaluate_step):
        --eval-episodes N         Evaluation episodes (default: 10)

PER-STEP COMPUTE TARGETS (overrides pipeline.yaml defaults):
        --compute-preprocess TGT  Compute target for the preprocess step
        --compute-train TGT       Compute target for the train step
        --compute-evaluate TGT    Compute target for the evaluate step
        --compute-register TGT    Compute target for the register step
                                  (only with --with-register)
        --compute TGT             Apply the same target to ALL steps
                                  (shortcut; per-step flags override this)

AZURE CONTEXT:
        --subscription-id ID      Azure subscription ID
        --resource-group NAME     Azure resource group
        --workspace-name NAME     Azure ML workspace
        --experiment-name NAME    Experiment name override
        --display-name NAME       Display name override

ADVANCED:
        --mlflow-token-retries N  MLflow token refresh retries (default: 3)
        --mlflow-http-timeout N   MLflow HTTP timeout in seconds (default: 60)
        --stream                  Stream pipeline logs after submission
    -a, --save-as PATH            Write created job state YAML to PATH

GENERAL:
    -h, --help                    Show this help message
        --config-preview          Print configuration and exit

Values resolved: CLI > Environment variables > Terraform outputs
Additional arguments after -- are forwarded to az ml job create.

EXAMPLES:
    # Minimal 3-step pipeline (preprocess -> train -> evaluate)
    submit-azureml-lerobot-pipeline.sh \
      -d user/koch-pick-place \
      --dataset-asset azureml:koch-pick-place:3

    # 4-step pipeline with opt-in model registration
    submit-azureml-lerobot-pipeline.sh \
      -d user/koch-pick-place \
      --dataset-asset azureml:koch-pick-place:3 \
      --with-register \
      --register-model-name koch-pick-place-act

    # Diffusion policy with custom hyperparameters and dedicated GPU pool
    submit-azureml-lerobot-pipeline.sh \
      -d user/dataset \
      --dataset-asset azureml:dataset:1 \
      -p diffusion \
      --training-steps 50000 \
      --batch-size 16 \
      --compute-train azureml:gpu-cluster

    # Reuse a locked preprocessing config from a previous run
    submit-azureml-lerobot-pipeline.sh \
      -d user/dataset \
      --dataset-asset azureml:dataset:2 \
      --preprocessing-config azureml://.../preprocessing_config.yaml
EOF
}

#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------

ensure_ml_extension() {
  az extension show --name ml &>/dev/null ||
    fatal "Azure ML CLI extension not installed. Run: az extension add --name ml"
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

dataset_repo_id="${DATASET_REPO_ID:-}"
dataset_asset=""
preprocessing_config=""
with_register=false
register_model_name="${REGISTER_MODEL:-}"

policy_type="${POLICY_TYPE:-act}"
job_name="${JOB_NAME:-lerobot-pipeline}"
policy_repo_id="${POLICY_REPO_ID:-}"
lerobot_version="${LEROBOT_VERSION:-}"

training_steps="${TRAINING_STEPS:-}"
batch_size="${BATCH_SIZE:-}"
eval_freq="${EVAL_FREQ:-}"
save_freq="${SAVE_FREQ:-5000}"
mixed_precision="${MIXED_PRECISION:-no}"

eval_episodes="${EVAL_EPISODES:-10}"

compute_default="${AZUREML_COMPUTE:-$(get_compute_target)}"
compute_preprocess=""
compute_train=""
compute_evaluate=""
compute_register=""

subscription_id="${AZURE_SUBSCRIPTION_ID:-$(get_subscription_id)}"
resource_group="${AZURE_RESOURCE_GROUP:-$(get_resource_group)}"
workspace_name="${AZUREML_WORKSPACE_NAME:-$(get_azureml_workspace)}"
mlflow_retries="${MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES:-3}"
mlflow_timeout="${MLFLOW_HTTP_REQUEST_TIMEOUT:-60}"

# Pipeline component base images are defined inline (digest-pinned) in each
# component's `environment:` block, so no named environment is registered here.

experiment_name=""
display_name=""
stream_logs=false
save_as=""
config_preview=false
forward_args=()

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)                    show_help; exit 0 ;;
    -d|--dataset-repo-id)         dataset_repo_id="$2"; shift 2 ;;
    --dataset-asset)              dataset_asset="$2"; shift 2 ;;
    --preprocessing-config)       preprocessing_config="$2"; shift 2 ;;
    --with-register)              with_register=true; shift ;;
    --register-model-name)        register_model_name="$2"; shift 2 ;;
    -p|--policy-type)             policy_type="$2"; shift 2 ;;
    -j|--job-name)                job_name="$2"; shift 2 ;;
    --policy-repo-id)             policy_repo_id="$2"; shift 2 ;;
    --lerobot-version)            lerobot_version="$2"; shift 2 ;;
    --training-steps)             training_steps="$2"; shift 2 ;;
    --batch-size)                 batch_size="$2"; shift 2 ;;
    --eval-freq)                  eval_freq="$2"; shift 2 ;;
    --save-freq)                  save_freq="$2"; shift 2 ;;
    --mixed-precision)            mixed_precision="$2"; shift 2 ;;
    --eval-episodes)              eval_episodes="$2"; shift 2 ;;
    --compute)                    compute_default="$2"; shift 2 ;;
    --compute-preprocess)         compute_preprocess="$2"; shift 2 ;;
    --compute-train)              compute_train="$2"; shift 2 ;;
    --compute-evaluate)           compute_evaluate="$2"; shift 2 ;;
    --compute-register)           compute_register="$2"; shift 2 ;;
    --subscription-id)            subscription_id="$2"; shift 2 ;;
    --resource-group)             resource_group="$2"; shift 2 ;;
    --workspace-name)             workspace_name="$2"; shift 2 ;;
    --mlflow-token-retries)       mlflow_retries="$2"; shift 2 ;;
    --mlflow-http-timeout)        mlflow_timeout="$2"; shift 2 ;;
    --experiment-name)            experiment_name="$2"; shift 2 ;;
    --display-name)               display_name="$2"; shift 2 ;;
    --stream)                     stream_logs=true; shift ;;
    -a|--save-as)                 save_as="$2"; shift 2 ;;
    --config-preview)             config_preview=true; shift ;;
    --)                           shift; forward_args=("$@"); break ;;
    *)                            fatal "Unknown option: $1" ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

require_tools az
ensure_ml_extension

[[ -n "$subscription_id" ]] || fatal "AZURE_SUBSCRIPTION_ID required"
[[ -n "$resource_group" ]] || fatal "AZURE_RESOURCE_GROUP required"
[[ -n "$workspace_name" ]] || fatal "AZUREML_WORKSPACE_NAME required"

[[ -n "$dataset_repo_id" ]] || fatal "--dataset-repo-id is required"
[[ -n "$dataset_asset" ]] || fatal "--dataset-asset is required (pipeline expects one uri_folder dataset)"

case "$policy_type" in
  act|diffusion|pi0) ;;
  *) fatal "Unsupported policy type: $policy_type (use: act, diffusion, pi0)" ;;
esac

case "$mixed_precision" in
  no|fp16|bf16) ;;
  *) fatal "--mixed-precision must be one of: no, fp16, bf16 (got '$mixed_precision')" ;;
esac

# AzureML model names: alphanumeric, dash, dot, underscore; must start with an
# alphanumeric or underscore; max 255 chars.
if [[ -n "$register_model_name" ]]; then
  [[ "$register_model_name" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,254}$ ]] || fatal \
    "--register-model-name: invalid model name '$register_model_name'. Must start with an alphanumeric or underscore and contain only [A-Za-z0-9._-] (max 255 chars)."
fi

if [[ "$with_register" == "true" && -z "$register_model_name" ]]; then
  fatal "--with-register requires --register-model-name NAME."
fi

if [[ "$with_register" != "true" && -n "$register_model_name" ]]; then
  warn "--register-model-name was set but --with-register is off; the register step will not run."
fi

if [[ "$with_register" != "true" && -n "$compute_register" ]]; then
  warn "--compute-register was set but --with-register is off; ignored."
fi

# Validate dataset asset URI form (same as training submit script)
_VALID_VERSION_RE='^([1-9][0-9]*|0)$'
case "$dataset_asset" in
  azureml://*/data/*/versions/*)
    version="${dataset_asset##*/versions/}"
    [[ "$version" =~ $_VALID_VERSION_RE ]] || fatal \
      "--dataset-asset: version must be a canonical integer with no leading zeros (got '$dataset_asset'). Use azureml://.../data/NAME/versions/VERSION."
    ;;
  azureml:*:*)
    version="${dataset_asset##*:}"
    [[ "$version" =~ $_VALID_VERSION_RE ]] || fatal \
      "--dataset-asset: version must be a canonical integer with no leading zeros (got '$dataset_asset'). Use azureml:NAME:VERSION; @latest and shorthands are not accepted."
    ;;
  *)
    fatal "--dataset-asset: unsupported URI form '$dataset_asset'. Use azureml:NAME:VERSION or azureml://.../data/NAME/versions/VERSION."
    ;;
esac

# Resolve per-step compute defaults
compute_preprocess="${compute_preprocess:-$compute_default}"
compute_train="${compute_train:-$compute_default}"
compute_evaluate="${compute_evaluate:-$compute_default}"
compute_register="${compute_register:-$compute_default}"

# At least one compute target must be resolvable; the pipeline.yaml defaults
# (azureml:cpu-cluster) are placeholders and not present in real workspaces.
[[ -n "$compute_train" ]] || fatal \
  "--compute-train (or --compute, or AZUREML_COMPUTE, or Terraform outputs) is required."

# Pipeline YAML selection
if [[ "$with_register" == "true" ]]; then
  pipeline_yaml="$REPO_ROOT/training/il/workflows/azureml/lerobot-pipeline-with-register.yaml"
else
  pipeline_yaml="$REPO_ROOT/training/il/workflows/azureml/lerobot-pipeline.yaml"
fi
[[ -f "$pipeline_yaml" ]] || fatal "Pipeline file not found: $pipeline_yaml"

#------------------------------------------------------------------------------
# Config Preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Pipeline" "$(basename "$pipeline_yaml")"
  print_kv "Dataset Asset" "$dataset_asset"
  print_kv "Dataset Repo Id" "$dataset_repo_id"
  print_kv "Preprocessing Config" "${preprocessing_config:-<none>}"
  print_kv "Policy Type" "$policy_type"
  print_kv "Job Name" "$job_name"
  print_kv "Policy Repo Id" "${policy_repo_id:-<none>}"
  print_kv "Training Steps" "${training_steps:-<default>}"
  print_kv "Batch Size" "${batch_size:-<default>}"
  print_kv "Eval Freq" "${eval_freq:-<default>}"
  print_kv "Save Freq" "$save_freq"
  print_kv "Mixed Precision" "$mixed_precision"
  print_kv "Eval Episodes" "$eval_episodes"
  print_kv "Compute Preprocess" "$compute_preprocess"
  print_kv "Compute Train" "$compute_train"
  print_kv "Compute Evaluate" "$compute_evaluate"
  if [[ "$with_register" == "true" ]]; then
    print_kv "Compute Register" "$compute_register"
    print_kv "Register Model Name" "$register_model_name"
  fi
  print_kv "Subscription" "$subscription_id"
  print_kv "Resource Group" "$resource_group"
  print_kv "Workspace" "$workspace_name"
  exit 0
fi

#------------------------------------------------------------------------------
# Build Submission Command
#
# Pipeline-level inputs use `--set inputs.X=Y`. Per-step overrides (compute
# and environment_variables) use `--set jobs.STEP.X=Y`. The Azure ML
# Kubernetes extension does NOT substitute ${{parent.inputs.X}} template refs
# inside `environment_variables` at runtime, so the script sets per-step env
# vars directly (mirroring the dual-injection pattern in the existing
# submit-azureml-lerobot-training.sh).
#------------------------------------------------------------------------------

az_args=(
  az ml job create
  --resource-group "$resource_group"
  --workspace-name "$workspace_name"
  --file "$pipeline_yaml"
)

[[ -n "$experiment_name" ]] && az_args+=(--set "experiment_name=$experiment_name")
[[ -n "$display_name" ]] && az_args+=(--set "display_name=$display_name")

# Pipeline-level inputs (visible in AML Studio UI)
az_args+=(
  --set "inputs.dataset.path=$dataset_asset"
  --set "inputs.dataset_repo_id=$dataset_repo_id"
  --set "inputs.policy_type=$policy_type"
  --set "inputs.job_name=$job_name"
  --set "inputs.compute_preprocess=$compute_preprocess"
  --set "inputs.compute_train=$compute_train"
  --set "inputs.compute_evaluate=$compute_evaluate"
)
# preprocessing_config is an optional input on the preprocess component (not a
# pipeline-level input, which cannot be optional), so override the step input directly.
[[ -n "$preprocessing_config" ]] && az_args+=(--set "jobs.preprocess_step.inputs.preprocessing_config.path=$preprocessing_config")

if [[ "$with_register" == "true" ]]; then
  az_args+=(
    --set "inputs.register_model_name=$register_model_name"
    --set "inputs.compute_register=$compute_register"
  )
fi

# Per-step compute overrides (belt-and-suspenders alongside pipeline inputs;
# some AML schema versions resolve compute fields from inputs reliably, others
# require explicit step-level pinning).
az_args+=(
  --set "jobs.preprocess_step.compute=$compute_preprocess"
  --set "jobs.train_step.compute=$compute_train"
  --set "jobs.evaluate_step.compute=$compute_evaluate"
)
[[ "$with_register" == "true" ]] && az_args+=(--set "jobs.register_step.compute=$compute_register")

# Per-step environment_variables
#
# Mirror the dual-injection pattern: every var the entry script reads is set
# directly on the step (the K8s extension does not interpolate ${{...}} refs
# in env vars). Keep parity with submit-azureml-lerobot-training.sh.

# preprocess_step: the Component passes inputs as CLI args, so it needs no
# extra env vars beyond what the Component default environment provides.

# train_step
az_args+=(
  --set "jobs.train_step.environment_variables.DATASET_REPO_ID=$dataset_repo_id"
  --set "jobs.train_step.environment_variables.POLICY_TYPE=$policy_type"
  --set "jobs.train_step.environment_variables.JOB_NAME=$job_name"
  --set "jobs.train_step.environment_variables.SAVE_FREQ=$save_freq"
  --set "jobs.train_step.environment_variables.MIXED_PRECISION=$mixed_precision"
  --set "jobs.train_step.environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "jobs.train_step.environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "jobs.train_step.environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "jobs.train_step.environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "jobs.train_step.environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
)
[[ -n "$policy_repo_id" ]]      && az_args+=(--set "jobs.train_step.environment_variables.POLICY_REPO_ID=$policy_repo_id")
[[ -n "$lerobot_version" ]]     && az_args+=(--set "jobs.train_step.environment_variables.LEROBOT_VERSION=$lerobot_version")
[[ -n "$training_steps" ]]      && az_args+=(--set "jobs.train_step.environment_variables.TRAINING_STEPS=$training_steps")
[[ -n "$batch_size" ]]          && az_args+=(--set "jobs.train_step.environment_variables.BATCH_SIZE=$batch_size")
[[ -n "$eval_freq" ]]           && az_args+=(--set "jobs.train_step.environment_variables.EVAL_FREQ=$eval_freq")

# evaluate_step
az_args+=(
  --set "jobs.evaluate_step.environment_variables.DATASET_REPO_ID=$dataset_repo_id"
  --set "jobs.evaluate_step.environment_variables.POLICY_TYPE=$policy_type"
  --set "jobs.evaluate_step.environment_variables.JOB_NAME=$job_name"
  --set "jobs.evaluate_step.environment_variables.EVAL_EPISODES=$eval_episodes"
  --set "jobs.evaluate_step.environment_variables.MLFLOW_ENABLE=true"
  --set "jobs.evaluate_step.environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
  --set "jobs.evaluate_step.environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
  --set "jobs.evaluate_step.environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  --set "jobs.evaluate_step.environment_variables.MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES=$mlflow_retries"
  --set "jobs.evaluate_step.environment_variables.MLFLOW_HTTP_REQUEST_TIMEOUT=$mlflow_timeout"
)
[[ -n "$lerobot_version" ]] && az_args+=(--set "jobs.evaluate_step.environment_variables.LEROBOT_VERSION=$lerobot_version")

# register_step (opt-in)
if [[ "$with_register" == "true" ]]; then
  az_args+=(
    --set "jobs.register_step.environment_variables.REGISTER_MODEL=$register_model_name"
    --set "jobs.register_step.environment_variables.POLICY_TYPE=$policy_type"
    --set "jobs.register_step.environment_variables.JOB_NAME=$job_name"
    --set "jobs.register_step.environment_variables.AZURE_SUBSCRIPTION_ID=$subscription_id"
    --set "jobs.register_step.environment_variables.AZURE_RESOURCE_GROUP=$resource_group"
    --set "jobs.register_step.environment_variables.AZUREML_WORKSPACE_NAME=$workspace_name"
  )
fi

[[ ${#forward_args[@]} -gt 0 ]] && az_args+=("${forward_args[@]}")
[[ -n "$save_as" ]] && az_args+=(--save-as "$save_as")
az_args+=(--query "name" -o "tsv")

#------------------------------------------------------------------------------
# Submit Pipeline
#------------------------------------------------------------------------------

info "Submitting AzureML LeRobot pipeline job..."
info "  Pipeline: $(basename "$pipeline_yaml")"
info "  Dataset Asset: $dataset_asset"
info "  Dataset Repo Id: $dataset_repo_id"
info "  Policy Type: $policy_type"
info "  Job Name: $job_name"
[[ "$with_register" == "true" ]] && info "  Register Model: $register_model_name"

# shellcheck disable=SC2329  # invoked indirectly via `trap`
_interrupt_message() {
  error "Interrupted while waiting for az ml job create. The pipeline may have been submitted."
  error "Check: https://ml.azure.com/runs?wsid=/subscriptions/${subscription_id}/resourceGroups/${resource_group}/providers/Microsoft.MachineLearningServices/workspaces/${workspace_name}"
  exit 130
}
trap _interrupt_message INT TERM

job_result=$("${az_args[@]}") || fatal \
  "Pipeline submission failed (workspace=${workspace_name}, resource_group=${resource_group}, pipeline=${pipeline_yaml}). Re-run with '-- --debug' for verbose Azure CLI output."

trap - INT TERM

info "Pipeline submitted: $job_result"
info "Portal: https://ml.azure.com/runs/$job_result?wsid=/subscriptions/$subscription_id/resourceGroups/$resource_group/providers/Microsoft.MachineLearningServices/workspaces/$workspace_name"

if [[ "$stream_logs" == "true" ]]; then
  info "Streaming pipeline logs (Ctrl+C to stop)..."
  az ml job stream --name "$job_result" \
    --resource-group "$resource_group" --workspace-name "$workspace_name" || true
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Deployment Summary"
print_kv "Pipeline Job Name" "$job_result"
print_kv "Pipeline File" "$(basename "$pipeline_yaml")"
print_kv "Dataset Asset" "$dataset_asset"
print_kv "Dataset Repo Id" "$dataset_repo_id"
print_kv "Policy Type" "$policy_type"
print_kv "Job Name" "$job_name"
print_kv "Compute Preprocess" "$compute_preprocess"
print_kv "Compute Train" "$compute_train"
print_kv "Compute Evaluate" "$compute_evaluate"
[[ "$with_register" == "true" ]] && print_kv "Compute Register" "$compute_register"
[[ "$with_register" == "true" ]] && print_kv "Register Model Name" "$register_model_name"
print_kv "Workspace" "$workspace_name"
[[ -n "$save_as" ]] && print_kv "Saved Job YAML" "$save_as"
exit 0
