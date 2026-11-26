#!/usr/bin/env bash
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Orchestrate the zero-NGC, OSMO-native closed-loop AVOps workflow on Azure.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AVOPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS] <COMMAND>

Run the zero-NGC closed-loop AVOps workflow (Ingest -> ODD Before -> Cosmos 3 -> ODD After).
Requires only Hugging Face authentication (HF_TOKEN) and public container images.

COMMANDS:
    preflight                 Validate required tools and endpoint configuration
    stage1-probe              Probe input driving clips into clip_manifest.json (aliases: stage1, probe)
    stage2-odd-before         Run AVOPS/ODD against Cosmos-Reason2-2B and build jobs (aliases: stage2, odd-before)
    stage3-synthesize         Generate synthetic driving clips via Cosmos3-Nano-FP8 (aliases: stage3, synthesize)
    stage4-odd-after          Re-run AVOPS/ODD on augmented dataset and compute delta (aliases: stage4, odd-after)
    run-all                   Execute stages 1 through 4 end-to-end
    osmo-submit               Submit the unified OSMO closed-loop workflow specification

OPTIONS:
    -h, --help                 Show this help message
    -e, --env-file PATH        Path to closed-loop env file (default: AVOPS/configs/closed-loop.env)
    -i, --input-dir DIR        Directory containing baseline driving video clips
    -o, --work-dir DIR         Output directory for closed-loop artifacts
    --max-clips N              Maximum Cosmos 3 synthetic clips to generate (default: 6)
    --vlm-url URL              Cosmos-Reason2-2B OpenAI endpoint (default: http://127.0.0.1:8010/v1)
    --cosmos3-url URL          Cosmos3-Nano-FP8 vLLM-Omni endpoint (default: http://127.0.0.1:8000)
    --mode MODE                Cosmos 3 generation mode: auto, i2v, or t2v (default: auto)
    --osmo-pool POOL           OSMO pool for osmo-submit (default: default)
    --dry-run                  Print commands without executing external calls
    --config-preview           Print resolved configuration and exit

EXAMPLES:
    $(basename "$0") --config-preview
    $(basename "$0") --dry-run -i ./clips -o ./out run-all
    $(basename "$0") -i ./clips -o ./out run-all
EOF
}

# Defaults (CLI overrides stay empty until parsed so precedence is CLI > env > env-file > default)
env_file="${AVOPS_CLOSED_LOOP_ENV:-$AVOPS_DIR/configs/closed-loop.env}"
env_file_explicit="false"
cli_input_dir=""
cli_work_dir=""
cli_max_clips=""
cli_vlm_url=""
cli_cosmos3_url=""
cli_cosmos3_mode=""
cli_osmo_pool=""
dry_run="false"
config_preview="false"
command_name=""

load_env_file() {
  local file="$1" line key val
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    val="${BASH_REMATCH[2]}"
    if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi
    [[ -n "${!key:-}" || -z "$val" ]] && continue
    export "$key=$val"
  done < "$file"
}

resolve_abs_path() {
  local target="$1"
  if [[ -d "$target" ]]; then
    (cd "$target" && pwd)
  elif [[ "$target" == /* ]]; then
    printf '%s\n' "$target"
  else
    printf '%s/%s\n' "$(pwd)" "${target#./}"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    -e|--env-file)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      env_file="$2"
      env_file_explicit="true"
      shift 2
      ;;
    -i|--input-dir)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_input_dir="$2"
      shift 2
      ;;
    -o|--work-dir)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_work_dir="$2"
      shift 2
      ;;
    --max-clips)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_max_clips="$2"
      shift 2
      ;;
    --vlm-url)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_vlm_url="$2"
      shift 2
      ;;
    --cosmos3-url)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_cosmos3_url="$2"
      shift 2
      ;;
    --mode)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_cosmos3_mode="$2"
      shift 2
      ;;
    --osmo-pool)
      [[ $# -ge 2 ]] || fatal "Option $1 requires a value"
      cli_osmo_pool="$2"
      shift 2
      ;;
    --dry-run)
      dry_run="true"
      shift
      ;;
    --config-preview)
      config_preview="true"
      shift
      ;;
    preflight)
      command_name="preflight"
      shift
      ;;
    stage1-probe|stage1|probe)
      command_name="stage1-probe"
      shift
      ;;
    stage2-odd-before|stage2|odd-before)
      command_name="stage2-odd-before"
      shift
      ;;
    stage3-synthesize|stage3|synthesize)
      command_name="stage3-synthesize"
      shift
      ;;
    stage4-odd-after|stage4|odd-after)
      command_name="stage4-odd-after"
      shift
      ;;
    run-all)
      command_name="run-all"
      shift
      ;;
    osmo-submit)
      command_name="osmo-submit"
      shift
      ;;
    *)
      fatal "Unknown option: $1"
      ;;
  esac
done

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

if [[ "$env_file_explicit" == "true" && ! -f "$env_file" ]]; then
  fatal "Environment file not found: $env_file"
fi

load_env_file "$env_file"

input_dir="${cli_input_dir:-${AVOPS_INPUT_DIR:-$AVOPS_DIR/data/clips}}"
work_dir="${cli_work_dir:-${AVOPS_WORK_DIR:-$AVOPS_DIR/data/closed-loop-run}}"
odd_image="${ODD_IMAGE:-odd-component:latest}"
vlm_url="${cli_vlm_url:-${ODD_VLM_URL:-http://127.0.0.1:8010/v1}}"
vlm_model="${ODD_VLM_MODEL:-nvidia/Cosmos-Reason2-2B}"
vlm_revision="${ODD_VLM_REVISION:-9ce19a195e423419c349abfc86fd07178b230561}"
vlm_input="${ODD_VLM_INPUT:-video}"
odd_threshold="${ODD_THRESHOLD:-0.8}"
cosmos3_url="${cli_cosmos3_url:-${COSMOS3_URL:-http://127.0.0.1:8000}}"
cosmos3_revision="${COSMOS3_REVISION:-9d45b0910219989a76c56aae82a08007bc1b597e}"
cosmos3_size="${COSMOS3_SIZE:-854x480}"
cosmos3_frames="${COSMOS3_NUM_FRAMES:-97}"
cosmos3_fps="${COSMOS3_FPS:-24}"
cosmos3_steps="${COSMOS3_STEPS:-35}"
cosmos3_guidance="${COSMOS3_GUIDANCE:-6.0}"
cosmos3_flow_shift="${COSMOS3_FLOW_SHIFT:-10.0}"
cosmos3_seed="${COSMOS3_SEED:-17}"
cosmos3_timeout="${COSMOS3_TIMEOUT:-900}"
cosmos3_mode="${cli_cosmos3_mode:-${COSMOS3_MODE:-auto}}"
max_clips="${cli_max_clips:-${AVOPS_MAX_CLIPS:-6}}"
osmo_pool="${cli_osmo_pool:-${OSMO_POOL:-default}}"

case "$cosmos3_mode" in
  auto|i2v|t2v) ;;
  *) fatal "Unsupported COSMOS3_MODE: $cosmos3_mode (use auto, i2v, or t2v)" ;;
esac

helper_py="$SCRIPT_DIR/cosmos3_closed_loop.py"
osmo_spec="$AVOPS_DIR/workflows/osmo/avops-closed-loop.yaml"

if [[ "$config_preview" == "true" ]]; then
  section "Closed-Loop AVOps Configuration Preview"
  print_kv "Env File" "$env_file"
  print_kv "Input Dir" "$input_dir"
  print_kv "Work Dir" "$work_dir"
  print_kv "ODD Image" "$odd_image"
  print_kv "VLM Endpoint" "$vlm_url ($vlm_model @ $vlm_revision, input=$vlm_input)"
  print_kv "ODD Threshold" "$odd_threshold"
  print_kv "Cosmos 3 Endpoint" "$cosmos3_url ($cosmos3_size, $cosmos3_frames frames, mode=$cosmos3_mode)"
  print_kv "Cosmos 3 Revision" "$cosmos3_revision"
  print_kv "Cosmos 3 Sampling" "fps=$cosmos3_fps steps=$cosmos3_steps guidance=$cosmos3_guidance flow_shift=$cosmos3_flow_shift seed=$cosmos3_seed timeout=${cosmos3_timeout}s"
  print_kv "Max Synthetic Clips" "$max_clips"
  print_kv "OSMO Pool" "$osmo_pool"
  print_kv "OSMO Workflow Spec" "$osmo_spec"
  print_kv "NGC Required" "No (zero-NGC mode)"
  exit 0
fi

[[ -n "$command_name" ]] || { show_help; fatal "A command is required"; }

run_cmd() {
  if [[ "$dry_run" == "true" ]]; then
    printf '[DRY-RUN] %s\n' "$*"
    return 0
  fi
  "$@"
}

stage_videos() {
  local src_dir="$1" dst_dir="$2" count=0 clip=""
  [[ -d "$src_dir" ]] || fatal "Video directory not found: $src_dir"
  while IFS= read -r -d '' clip; do
    cp -- "$clip" "$dst_dir/"
    count=$((count + 1))
  done < <(find "$src_dir" -maxdepth 1 -type f \
    \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.avi' -o -iname '*.mkv' -o -iname '*.webm' \) \
    -print0)
  [[ "$count" -gt 0 ]] || fatal "No supported video clips found in: $src_dir"
}

run_odd_container() {
  local clips_in="$1" odd_out="$2" abs_clips abs_out
  if [[ "$dry_run" != "true" ]]; then
    [[ -d "$clips_in" ]] || fatal "Input clips directory not found: $clips_in"
    mkdir -p "$odd_out"
    if ! docker image inspect "$odd_image" >/dev/null 2>&1; then
      info "Building $odd_image from $AVOPS_DIR/ODD"
      docker build -t "$odd_image" -f "$AVOPS_DIR/ODD/Dockerfile" "$AVOPS_DIR/ODD"
    fi
  fi
  abs_clips="$(resolve_abs_path "$clips_in")"
  abs_out="$(resolve_abs_path "$odd_out")"
  run_cmd docker run --rm --network host --user "$(id -u):$(id -g)" \
    -v "$abs_clips:/input:ro" \
    -v "$abs_out:/output" \
    -e "VLM_URL=$vlm_url" \
    -e "VLM_MODEL=$vlm_model" \
    -e "VLM_INPUT=$vlm_input" \
    "$odd_image" \
    --input-dir /input \
    --output-dir /output \
    --threshold "$odd_threshold" \
    --host-video-dir "$abs_clips"
}

stage1_probe() {
  section "Stage 1/4: Ingest & Clip Probe"
  require_tools python3
  run_cmd python3 "$helper_py" probe-clips \
    --input-dir "$input_dir" \
    --out "$work_dir/clip_manifest.json"
}

stage2_odd_before() {
  section "Stage 2/4: ODD Gap Analysis (Before) with $vlm_model"
  require_tools python3
  [[ "$dry_run" == "true" ]] || require_tools docker
  run_odd_container "$input_dir" "$work_dir/odd-before"
  local build_args=(
    python3 "$helper_py" build-jobs
    --odd-output "$work_dir/odd-before"
    --out "$work_dir/cosmos3_jobs.json"
    --max-clips "$max_clips"
    --mode "$cosmos3_mode"
    --video-dir "$input_dir"
  )
  run_cmd "${build_args[@]}"
}

stage3_synthesize() {
  section "Stage 3/4: Targeted Synthetic Video Generation (Cosmos3-Nano-FP8)"
  require_tools python3
  run_cmd python3 "$helper_py" synthesize \
    --jobs "$work_dir/cosmos3_jobs.json" \
    --endpoint-url "$cosmos3_url" \
    --out-dir "$work_dir/synthetic-clips" \
    --size "$cosmos3_size" \
    --num-frames "$cosmos3_frames" \
    --fps "$cosmos3_fps" \
    --steps "$cosmos3_steps" \
    --guidance-scale "$cosmos3_guidance" \
    --flow-shift "$cosmos3_flow_shift" \
    --seed "$cosmos3_seed" \
    --timeout "$cosmos3_timeout" \
    --mode "$cosmos3_mode"
}

stage4_odd_after() {
  section "Stage 4/4: Closed-Loop Verification & ODD Re-Evaluation (After)"
  require_tools python3
  [[ "$dry_run" == "true" ]] || require_tools docker
  local augmented_dir="$work_dir/augmented-clips"
  if [[ "$dry_run" == "true" ]]; then
    printf '[DRY-RUN] stage baseline (%s) + synthetic (%s) clips into %s\n' \
      "$input_dir" "$work_dir/synthetic-clips" "$augmented_dir"
  else
    rm -rf "$augmented_dir"
    mkdir -p "$augmented_dir"
    stage_videos "$input_dir" "$augmented_dir"
    stage_videos "$work_dir/synthetic-clips" "$augmented_dir"
  fi
  run_odd_container "$augmented_dir" "$work_dir/odd-after"
  run_cmd python3 "$helper_py" compare-odd \
    --before-dir "$work_dir/odd-before" \
    --after-dir "$work_dir/odd-after" \
    --synthesis-manifest "$work_dir/synthetic-clips/synthesis_manifest.json" \
    --out-dir "$work_dir/report"
}

#------------------------------------------------------------------------------
# Main Logic
#------------------------------------------------------------------------------

case "$command_name" in
  preflight)
    section "Preflight Checks (Zero-NGC Mode)"
    if [[ "$dry_run" != "true" ]]; then
      require_tools python3 docker
      python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || fatal "Python 3.11 or newer is required"
      info "Preflight passed (no NGC_API_KEY required)"
    else
      printf '[DRY-RUN] verified tools: python3 docker\n'
    fi
    ;;
  stage1-probe)
    stage1_probe
    ;;
  stage2-odd-before)
    stage2_odd_before
    ;;
  stage3-synthesize)
    stage3_synthesize
    ;;
  stage4-odd-after)
    stage4_odd_after
    ;;
  run-all)
    stage1_probe
    stage2_odd_before
    stage3_synthesize
    stage4_odd_after
    ;;
  osmo-submit)
    section "Submit Unified OSMO Closed-Loop Workflow"
    [[ "$dry_run" == "true" ]] || require_tools osmo
    osmo_args=(
      osmo workflow submit "$osmo_spec"
      --pool "$osmo_pool"
      --set-string "odd_image=$odd_image"
      --set-string "vlm_model=$vlm_model"
      --set-string "vlm_revision=$vlm_revision"
      --set-string "vlm_input=$vlm_input"
      --set-string "odd_threshold=$odd_threshold"
      --set-string "cosmos3_revision=$cosmos3_revision"
      --set-string "max_clips=$max_clips"
      --set-string "video_size=$cosmos3_size"
      --set-string "num_frames=$cosmos3_frames"
      --set-string "fps=$cosmos3_fps"
      --set-string "steps=$cosmos3_steps"
      --set-string "generation_mode=$cosmos3_mode"
    )
    run_cmd "${osmo_args[@]}"
    ;;
esac

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------

section "Summary"
print_kv "Command" "$command_name"
print_kv "Work Dir" "$work_dir"
print_kv "Dry Run" "$dry_run"
info "AVOps closed-loop step completed"
