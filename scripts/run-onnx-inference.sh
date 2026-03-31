#!/bin/bash
# Run exported ONNX or JIT model against Isaac Sim environment
# Usage: ./run-onnx-inference.sh [--video] [--max-steps N] [--format onnx|jit]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Default paths
MODEL="${MODEL:-logs/rsl_rl/ant/exported/policy.onnx}"
FORMAT="${FORMAT:-}"
TASK="${TASK:-Isaac-Ant-v0}"
NUM_ENVS="${NUM_ENVS:-16}"
MAX_STEPS="${MAX_STEPS:-500}"

# Parse arguments
VIDEO_FLAG=""
USE_GPU_FLAG=""
EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --video)
            VIDEO_FLAG="--video"
            shift
            ;;
        --use-gpu)
            USE_GPU_FLAG="--use-gpu"
            shift
            ;;
        --max-steps)
            MAX_STEPS="$2"
            shift 2
            ;;
        --num-envs)
            NUM_ENVS="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --format)
            FORMAT="$2"
            shift 2
            ;;
        *)
            EXTRA_ARGS="$EXTRA_ARGS $1"
            shift
            ;;
    esac
done

# Build format flag
FORMAT_FLAG=""
if [[ -n "$FORMAT" ]]; then
    FORMAT_FLAG="--format $FORMAT"
fi

echo "=============================================="
echo "Policy Inference Test"
echo "=============================================="
echo "Task: $TASK"
echo "Model: $MODEL"
echo "Format: ${FORMAT:-auto-detect}"
echo "Num Envs: $NUM_ENVS"
echo "Max Steps: $MAX_STEPS"
echo "=============================================="

# Check if model exists
if [[ ! -f "$MODEL" ]]; then
    echo "ERROR: Model not found at: $MODEL"
    echo "Run the export script first:"
    echo "  .venv/bin/python src/inference/scripts/export_policy.py --checkpoint logs/rsl_rl/ant/YYYY-MM-DD_HH-MM-SS/model_XXXX.pt"
    exit 1
fi

# Run with Isaac Sim Python
ISAAC_SIM_PYTHON="${ISAAC_SIM_PYTHON:-$HOME/.local/share/ov/pkg/isaac-sim-4.5.0/python.sh}"
"$ISAAC_SIM_PYTHON" \
    src/inference/scripts/play_policy.py \
    --task "$TASK" \
    --num_envs "$NUM_ENVS" \
    --model "$MODEL" \
    --max-steps "$MAX_STEPS" \
    --headless \
    $VIDEO_FLAG \
    $USE_GPU_FLAG \
    $FORMAT_FLAG \
    $EXTRA_ARGS
