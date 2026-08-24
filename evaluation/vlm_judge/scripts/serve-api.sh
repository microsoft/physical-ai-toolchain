#!/usr/bin/env bash
# Launch the VLM-as-judge HTTP API.
# Defaults to the echo backend so the API can boot without GPUs.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"
# shellcheck source=../../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS]

Start the VLM-as-judge FastAPI server.

OPTIONS:
    -h, --help              Show this help
    --host HOST             Bind host (default: 0.0.0.0)
    --port PORT             Bind port (default: 8088)
    --backend NAME          qwen3-vl | openai-compat | echo (default: echo)
    --model-id ID           Backend model id
    --base-url URL          OpenAI-compatible base URL
    --reload                Enable uvicorn auto-reload (development only)
    --config-preview        Print configuration and exit

ENV:
    VLM_JUDGE_BACKEND, VLM_JUDGE_MODEL_ID, VLM_JUDGE_BASE_URL,
    VLM_JUDGE_API_KEY, VLM_JUDGE_N_FRAMES, VLM_JUDGE_CACHE_DIR.

EXAMPLES:
    $(basename "$0") --backend echo
    $(basename "$0") --backend openai-compat \\
        --base-url http://localhost:8000/v1 \\
        --model-id Qwen/Qwen3-VL-30B-A3B-Instruct
EOF
}

host="0.0.0.0"
port="8088"
backend="${VLM_JUDGE_BACKEND:-echo}"
model_id="${VLM_JUDGE_MODEL_ID:-Qwen/Qwen3-VL-4B-Instruct}"
base_url="${VLM_JUDGE_BASE_URL:-}"
reload=false
config_preview=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)         show_help; exit 0 ;;
    --host)            host="$2"; shift 2 ;;
    --port)            port="$2"; shift 2 ;;
    --backend)         backend="$2"; shift 2 ;;
    --model-id)        model_id="$2"; shift 2 ;;
    --base-url)        base_url="$2"; shift 2 ;;
    --reload)          reload=true; shift ;;
    --config-preview)  config_preview=true; shift ;;
    *)                 fatal "Unknown option: $1 (use --help)" ;;
  esac
done

require_tools python3

export VLM_JUDGE_BACKEND="$backend"
export VLM_JUDGE_MODEL_ID="$model_id"
[[ -n "$base_url" ]] && export VLM_JUDGE_BASE_URL="$base_url"

section "VLM Judge API Configuration"
print_kv "Host"       "$host"
print_kv "Port"       "$port"
print_kv "Backend"    "$backend"
print_kv "Model"      "$model_id"
print_kv "Base URL"   "${base_url:-(n/a)}"
print_kv "Reload"     "$reload"

[[ "$config_preview" == "true" ]] && exit 0

cmd=(python3 -m uvicorn evaluation.vlm_judge.api:app --host "$host" --port "$port")
[[ "$reload" == "true" ]] && cmd+=(--reload)

cd "$REPO_ROOT"
exec "${cmd[@]}"
