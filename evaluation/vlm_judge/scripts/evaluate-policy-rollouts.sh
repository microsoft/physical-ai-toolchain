#!/usr/bin/env bash
# Run the VLM judge against a directory of policy-rollout MP4s.
# Defaults align with leisaac-tests/pickup-orange/ but accept any --rollout-root.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Score policy-rollout MP4s with the VLM-as-judge harness.

OPTIONS:
    -h, --help               Show this help
    -r, --rollout-root DIR   Rollout directory (default: leisaac-tests/pickup-orange)
    -i, --instruction TEXT   Default instruction (default: Grab orange and place into plate)
    --instructions-file PATH JSON map of episode_id -> instruction
    -o, --output PATH        Output JSONL (default: outputs/vlm-judge/<rollout>.jsonl)
    -b, --backend NAME       qwen3-vl | openai-compat | echo
    -m, --model-id ID        Model id (default: Qwen/Qwen3-VL-4B-Instruct)
    -n, --n-frames N         Frames per rollout (default: 12)
    -l, --limit N            Cap number of rollouts
    --base-url URL           OpenAI-compatible base URL
    --force                  Ignore cache
    --dry-run                Discover rollouts but skip inference
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") --limit 1
    $(basename "$0") --backend echo --dry-run
EOF
}

rollout_root="$REPO_ROOT/leisaac-tests/pickup-orange"
instruction="Grab orange and place into plate"
instructions_file=""
output=""
backend="${VLM_JUDGE_BACKEND:-qwen3-vl}"
model_id="${VLM_JUDGE_MODEL_ID:-Qwen/Qwen3-VL-4B-Instruct}"
n_frames=12
limit=""
base_url=""
force=false
dry_run=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    -r|--rollout-root)      rollout_root="$2"; shift 2 ;;
    -i|--instruction)       instruction="$2"; shift 2 ;;
    --instructions-file)    instructions_file="$2"; shift 2 ;;
    -o|--output)            output="$2"; shift 2 ;;
    -b|--backend)           backend="$2"; shift 2 ;;
    -m|--model-id)          model_id="$2"; shift 2 ;;
    -n|--n-frames)          n_frames="$2"; shift 2 ;;
    -l|--limit)             limit="$2"; shift 2 ;;
    --base-url)             base_url="$2"; shift 2 ;;
    --force)                force=true; shift ;;
    --dry-run)              dry_run=true; shift ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      fatal "Unknown option: $1 (use --help)" ;;
  esac
done

[[ -d "$rollout_root" ]] || fatal "Rollout root not found: $rollout_root"
rollout_name="$(basename "${rollout_root%/}")"
output="${output:-$REPO_ROOT/outputs/vlm-judge/policy-${rollout_name}.jsonl}"

require_tools python3

section "Policy-Rollout Judge Configuration"
print_kv "Rollout root"     "$rollout_root"
print_kv "Output"           "$output"
print_kv "Instruction"      "$instruction"
print_kv "Instructions file" "${instructions_file:-(none)}"
print_kv "Backend"          "$backend"
print_kv "Model"            "$model_id"
print_kv "Frames/rollout"   "$n_frames"
print_kv "Limit"            "${limit:-(all)}"
print_kv "Base URL"         "${base_url:-(n/a)}"
print_kv "Force"            "$force"
print_kv "Dry run"          "$dry_run"

[[ "$config_preview" == "true" ]] && exit 0

cmd=(python3 -m evaluation.vlm_judge.policy_eval
     --rollout-root "$rollout_root"
     --instruction "$instruction"
     --output "$output"
     --backend "$backend"
     --model-id "$model_id"
     --n-frames "$n_frames")

[[ -n "$limit" ]] && cmd+=(--limit "$limit")
[[ -n "$instructions_file" ]] && cmd+=(--instructions-file "$instructions_file")
[[ -n "$base_url" ]] && cmd+=(--base-url "$base_url")
[[ "$force" == "true" ]] && cmd+=(--force)
[[ "$dry_run" == "true" ]] && cmd+=(--dry-run)

section "Running VLM Judge over Rollouts"
info "Cmd: ${cmd[*]}"
cd "$REPO_ROOT"
"${cmd[@]}"

section "Deployment Summary"
print_kv "Rollout root" "$rollout_root"
print_kv "Output"       "$output"
print_kv "Backend"      "$backend"
