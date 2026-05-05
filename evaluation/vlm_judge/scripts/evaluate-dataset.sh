#!/usr/bin/env bash
# Generic VLM-as-judge evaluation wrapper.
# Resolves the dataset directory and forwards remaining args to the Python CLI.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") --dataset PATH [OPTIONS] [-- EXTRA_ARGS]

Run the VLM-as-judge harness against a LeRobot dataset (v2.1 or v3.0).

OPTIONS:
    -h, --help               Show this help message
    -d, --dataset PATH       Dataset root (required)
    -o, --output PATH        Output JSONL (default: outputs/vlm-judge/<dataset>.jsonl)
    -b, --backend NAME       qwen3-vl | openai-compat | echo (default: qwen3-vl)
    -m, --model-id ID        Model id (default: Qwen/Qwen3-VL-4B-Instruct)
    -n, --n-frames N         Frames per episode (default: 12)
    -l, --limit N            Cap number of episodes (default: all)
    -v, --views VIEW...      Restrict to specific views (e.g. observation.images.front)
    --instruction TEXT       Override dataset-supplied instruction
    --base-url URL           OpenAI-compatible base URL (openai-compat backend)
    --dry-run                Resolve episodes + extract frames, skip inference
    --config-preview         Print resolved configuration and exit

ENV:
    VLM_JUDGE_BACKEND, VLM_JUDGE_MODEL_ID override defaults if exported.

EXAMPLES:
    $(basename "$0") --dataset datasets/leisaac-pick-orange --limit 3
    $(basename "$0") --dataset datasets/cnc_lerobot --backend echo --dry-run
EOF
}

dataset=""
output=""
backend="${VLM_JUDGE_BACKEND:-qwen3-vl}"
model_id="${VLM_JUDGE_MODEL_ID:-Qwen/Qwen3-VL-4B-Instruct}"
n_frames=12
limit=""
views=()
instruction=""
base_url=""
dry_run=false
config_preview=false
extra_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    -d|--dataset)      dataset="$2"; shift 2 ;;
    -o|--output)       output="$2"; shift 2 ;;
    -b|--backend)      backend="$2"; shift 2 ;;
    -m|--model-id)     model_id="$2"; shift 2 ;;
    -n|--n-frames)     n_frames="$2"; shift 2 ;;
    -l|--limit)        limit="$2"; shift 2 ;;
    -v|--views)
      shift
      while [[ $# -gt 0 && "$1" != -* ]]; do views+=("$1"); shift; done
      ;;
    --instruction)     instruction="$2"; shift 2 ;;
    --base-url)        base_url="$2"; shift 2 ;;
    --dry-run)         dry_run=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    --)                shift; extra_args=("$@"); break ;;
    *)                 fatal "Unknown option: $1 (use --help)" ;;
  esac
done

[[ -n "$dataset" ]] || fatal "--dataset is required"
[[ -d "$dataset" ]] || fatal "Dataset directory not found: $dataset"

dataset_name="$(basename "${dataset%/}")"
output="${output:-$REPO_ROOT/outputs/vlm-judge/${dataset_name}.jsonl}"

require_tools python3

section "VLM Judge Configuration"
print_kv "Dataset"        "$dataset"
print_kv "Output"         "$output"
print_kv "Backend"        "$backend"
print_kv "Model"          "$model_id"
print_kv "Frames/episode" "$n_frames"
print_kv "Limit"          "${limit:-(all)}"
print_kv "Views"          "${views[*]:-(all)}"
print_kv "Instruction"    "${instruction:-(from dataset meta)}"
print_kv "Base URL"       "${base_url:-(n/a)}"
print_kv "Dry run"        "$dry_run"

[[ "$config_preview" == "true" ]] && exit 0

cmd=(python3 -m evaluation.vlm_judge.run
     --dataset "$dataset"
     --output "$output"
     --backend "$backend"
     --model-id "$model_id"
     --n-frames "$n_frames")

[[ -n "$limit" ]] && cmd+=(--limit "$limit")
[[ -n "$instruction" ]] && cmd+=(--instruction "$instruction")
[[ -n "$base_url" ]] && cmd+=(--base-url "$base_url")
[[ ${#views[@]} -gt 0 ]] && cmd+=(--views "${views[@]}")
[[ "$dry_run" == "true" ]] && cmd+=(--dry-run)

if [[ ${#extra_args[@]} -gt 0 ]]; then
  cmd+=("${extra_args[@]}")
fi

section "Running VLM Judge"
info "Cmd: ${cmd[*]}"
cd "$REPO_ROOT"
"${cmd[@]}"

section "Deployment Summary"
print_kv "Dataset" "$dataset"
print_kv "Output"  "$output"
print_kv "Backend" "$backend"
