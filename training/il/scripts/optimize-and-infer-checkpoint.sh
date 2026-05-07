#!/usr/bin/env bash
# End-to-end recipe: pull a registered LeRobot policy from Azure ML, optimize
# it for faster inference, and run a benchmark inference call. Intended as a
# self-contained walkthrough you can hand to a teammate.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# `.env` next to this script can hold workspace overrides without polluting your
# shell. Keys mirror the AZURE_* / AZUREML_* env vars below. Optional.
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
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Pull a LeRobot policy checkpoint from Azure ML, run optimize.py against it,
and execute a benchmark inference call with infer.py.

REQUIRED:
    -m, --model-name NAME        Azure ML registered model name
                                 (e.g., hybrid-hack-smolvla-full)

OPTIONAL:
    -v, --model-version VERSION  Model version, or 'latest' (default: latest)
    -o, --output-dir DIR         Where to place the optimized artifact tree
                                 (default: outputs/optimized/<model>/v<version>)
    --device {cuda,cpu}          Inference device (default: cuda)
    --dtype {bf16,fp16,fp32}     Autocast dtype on CUDA (default: bf16)
    --compile-mode MODE          torch.compile mode: max-autotune, reduce-overhead,
                                 default. Ignored when --skip-compile is set.
                                 (default: max-autotune)
    --skip-compile               Skip torch.compile entirely. Recommended for the
                                 first run because compiling SmolVLA takes 10+ min.
    --skip-baseline              Skip the eager-mode baseline benchmark in optimize.py
    --warmup-iters N             AOT warmup iterations (default: 3)
    --bench-iters N              Benchmark iterations (default: 10)
    --no-inference               Stop after optimize.py; do not run infer.py
    --config-preview             Print resolved configuration and exit

AZURE CONTEXT (env vars or .env file):
    AZURE_SUBSCRIPTION_ID        Azure subscription containing the workspace
    AZURE_RESOURCE_GROUP         Resource group of the AML workspace
    AZUREML_WORKSPACE_NAME       AML workspace name
    AZURE_USE_CLI_CREDENTIAL     Set to "true" for local az-login auth (recommended)

EXAMPLES:
    # Quick test (skip compile so you get a result in a few minutes)
    $(basename "$0") -m hybrid-hack-smolvla-full --skip-compile

    # Full optimization with torch.compile (takes 10-15 min on first run)
    $(basename "$0") -m hybrid-hack-smolvla-full -v 16

    # Just convert, don't benchmark
    $(basename "$0") -m hybrid-hack-smolvla-full --no-inference
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

# Model identity. `model_version=latest` lets optimize.py resolve the highest
# version of the named model in the AML registry.
model_name=""
model_version="latest"
output_dir=""

# Inference runtime. bf16 autocast is a no-op for SmolVLA (model is already bf16
# on disk) but is the right default for ACT/Diffusion policies.
device="cuda"
dtype="bf16"
compile_mode="max-autotune"
skip_compile=false
skip_baseline=false
warmup_iters=3
bench_iters=10

# Workflow toggles
run_inference=true
config_preview=false

#------------------------------------------------------------------------------
# Argument parsing
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)            show_help; exit 0 ;;
    -m|--model-name)      model_name="$2"; shift 2 ;;
    -v|--model-version)   model_version="$2"; shift 2 ;;
    -o|--output-dir)      output_dir="$2"; shift 2 ;;
    --device)             device="$2"; shift 2 ;;
    --dtype)              dtype="$2"; shift 2 ;;
    --compile-mode)       compile_mode="$2"; shift 2 ;;
    --skip-compile)       skip_compile=true; shift ;;
    --skip-baseline)      skip_baseline=true; shift ;;
    --warmup-iters)       warmup_iters="$2"; shift 2 ;;
    --bench-iters)        bench_iters="$2"; shift 2 ;;
    --no-inference)       run_inference=false; shift ;;
    --config-preview)     config_preview=true; shift ;;
    *)                    fatal "Unknown option: $1" ;;
  esac
done

[[ -n "$model_name" ]] || fatal "Missing required --model-name. Run with --help for usage."

# Tools we touch directly. Python deps (azure-ai-ml, torch, lerobot) are checked
# at import time inside optimize.py / infer.py.
require_tools python az tmux

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

# Azure context. If you do not have a workspace yet, run:
#   source infrastructure/terraform/prerequisites/az-sub-init.sh
# from the repo root, then set the three AZURE/AZUREML variables.
subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
resource_group="${AZURE_RESOURCE_GROUP:-}"
workspace_name="${AZUREML_WORKSPACE_NAME:-}"
use_cli_credential="${AZURE_USE_CLI_CREDENTIAL:-true}"

if [[ -z "$subscription_id" || -z "$resource_group" || -z "$workspace_name" ]]; then
  fatal "Set AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME (e.g., in $(basename "$ENV_FILE"))."
fi

# Default output path mirrors the convention in optimize.py so infer.py can
# resolve it automatically: outputs/optimized/<model>/v<version>/
if [[ -z "$output_dir" ]]; then
  output_dir="$REPO_ROOT/outputs/optimized/$model_name/v$model_version"
fi

#------------------------------------------------------------------------------
# Config preview
#------------------------------------------------------------------------------

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Model name"          "$model_name"
  print_kv "Model version"       "$model_version"
  print_kv "Output directory"    "$output_dir"
  print_kv "Device"              "$device"
  print_kv "Dtype"               "$dtype"
  print_kv "Compile mode"        "$compile_mode"
  print_kv "Skip compile"        "$skip_compile"
  print_kv "Skip baseline"       "$skip_baseline"
  print_kv "Warmup iters"        "$warmup_iters"
  print_kv "Bench iters"         "$bench_iters"
  print_kv "Run inference"       "$run_inference"
  print_kv "Subscription"        "$subscription_id"
  print_kv "Resource group"      "$resource_group"
  print_kv "AML workspace"       "$workspace_name"
  print_kv "CLI credential"      "$use_cli_credential"
  exit 0
fi

#------------------------------------------------------------------------------
# Azure ML auth check
#------------------------------------------------------------------------------
section "Azure ML Authentication"

# `az account show` is the cheapest way to validate the user is logged in.
# Exit early so we do not waste time downloading a 500 MB model with no auth.
az account show --subscription "$subscription_id" --query name -o tsv >/dev/null 2>&1 || \
  fatal "Azure CLI is not logged in to subscription $subscription_id. Run: az login --tenant <tenant-id>"
info "Authenticated to subscription: $subscription_id"

#------------------------------------------------------------------------------
# Step 1: Optimize (download + compile + benchmark)
#------------------------------------------------------------------------------
section "Step 1: Optimize (download + benchmark)"

# Build the optimize.py argument list. Bash arrays preserve quoting through
# expansion which is important for paths with spaces.
optimize_args=(
  --model-name    "$model_name"
  --model-version "$model_version"
  --output-dir    "$output_dir"
  --device        "$device"
  --dtype         "$dtype"
  --compile-mode  "$compile_mode"
  --warmup-iters  "$warmup_iters"
  --bench-iters   "$bench_iters"
)
[[ "$skip_compile"  == "true" ]] && optimize_args+=(--skip-compile)
[[ "$skip_baseline" == "true" ]] && optimize_args+=(--skip-baseline)

# Export the Azure context so optimize.py / infer.py can construct the MLClient
# without each developer having to set them up by hand.
export AZURE_SUBSCRIPTION_ID="$subscription_id"
export AZURE_RESOURCE_GROUP="$resource_group"
export AZUREML_WORKSPACE_NAME="$workspace_name"
export AZURE_USE_CLI_CREDENTIAL="$use_cli_credential"

# Quiet HF tokenizer noise, force unbuffered logs so tmux/teamcity see progress.
export TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

# PYTHONPATH must point at the repo root so `python -m training.il...` resolves.
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

info "Running: python -m training.il.scripts.lerobot.optimize ${optimize_args[*]}"
( cd "$REPO_ROOT" && python -m training.il.scripts.lerobot.optimize "${optimize_args[@]}" )

# Sanity check: optimize.py writes this file when it finishes successfully.
profile_json="$output_dir/optimization_profile.json"
[[ -f "$profile_json" ]] || fatal "optimize.py finished but $profile_json is missing"
info "Optimization profile written to: $profile_json"

#------------------------------------------------------------------------------
# Step 2: Inference benchmark (optional)
#------------------------------------------------------------------------------

if [[ "$run_inference" == "true" ]]; then
  section "Step 2: Inference benchmark"

  # infer.py loads the runtime settings (device, dtype, compile mode, inductor
  # cache path) from optimization_profile.json. CLI flags here only override
  # what the profile already records.
  infer_args=(
    --optimized-dir "$output_dir"
    --bench
    --warmup-iters  "$warmup_iters"
    --bench-iters   "$bench_iters"
  )
  # If we skipped compile during optimize, also skip it in infer (otherwise
  # infer.py would pay the 10-min compile cost we deliberately avoided).
  [[ "$skip_compile" == "true" ]] && infer_args+=(--no-compile)

  info "Running: python -m training.il.scripts.lerobot.infer ${infer_args[*]}"
  ( cd "$REPO_ROOT" && python -m training.il.scripts.lerobot.infer "${infer_args[@]}" )
fi

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
section "Summary"
print_kv "Model"               "$model_name v$model_version"
print_kv "Optimized directory" "$output_dir"
print_kv "Profile JSON"        "$profile_json"
print_kv "Compile mode"        "$( [[ "$skip_compile" == "true" ]] && echo "skipped" || echo "$compile_mode" )"
print_kv "Inductor cache"      "$output_dir/inductor_cache"

# Hand off the next concrete command so the colleague does not have to look it up.
echo
info "To run additional inference calls without re-downloading or recompiling:"
echo "  python -m training.il.scripts.lerobot.infer --optimized-dir $output_dir$( [[ "$skip_compile" == "true" ]] && echo " --no-compile" )"
