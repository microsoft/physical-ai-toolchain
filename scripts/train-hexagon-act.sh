#!/usr/bin/env bash
# Train Hexagon ACT policy on converted HDF5 dataset
# Designed for VM execution with the aeon_il_training_for_microsoft codebase
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || dirname "$SCRIPT_DIR")"

#------------------------------------------------------------------------------
# Help
#------------------------------------------------------------------------------

show_help() {
  cat << 'EOF'
Usage: train-hexagon-act.sh [OPTIONS]

Train a Hexagon ACT policy on HDF5 episode data using the
aeon_il_training_for_microsoft codebase. Runs locally on a GPU VM.

REQUIRED:
    -d, --dataset-dir DIR         Path to HDF5 dataset directory

OPTIONS:
    -c, --ckpt-dir DIR            Checkpoint output directory (default: outputs/hexagon-act)
    -n, --num-epochs N            Training epochs (default: 10000)
    -b, --batch-size N            Batch size (default: 16)
        --lr LR                   Learning rate (default: 1e-5)
        --chunk-size N            Action chunk size (default: 30)
        --with-validation         Enable validation split
        --with-augmentation       Enable data augmentation
        --log-type TYPE           Logging: tensorboard, mlflow, none (default: tensorboard)
        --resume                  Resume from latest checkpoint
        --hexagon-repo DIR        Path to aeon_il_training_for_microsoft (auto-detected)
        --config-preview          Print configuration and exit
    -h, --help                    Show this help message

EXAMPLES:
    # Train on converted SUCCESS episodes
    train-hexagon-act.sh -d datasets/aeon_houston_hdf5_success

    # Custom epochs and batch size
    train-hexagon-act.sh -d datasets/aeon_houston_hdf5_success -n 5000 -b 8

    # Resume training with validation
    train-hexagon-act.sh -d datasets/aeon_houston_hdf5_success --resume --with-validation
EOF
}

#------------------------------------------------------------------------------
# Defaults
#------------------------------------------------------------------------------

dataset_dir=""
ckpt_dir="${REPO_ROOT}/outputs/hexagon-act"
num_epochs=10000
batch_size=16
lr="1e-5"
chunk_size=30
with_validation=false
with_augmentation=false
log_type="tensorboard"
resume=false
hexagon_repo=""
config_preview=false

# Auto-detect hexagon repo relative to this workspace
CANDIDATE_PATHS=(
  "${REPO_ROOT}/../northstar/aeon_il_training_for_microsoft"
  "${REPO_ROOT}/../hexagon/northstar/aeon_il_training_for_microsoft"
  "${REPO_ROOT}/../../hexagon/northstar/aeon_il_training_for_microsoft"
)

#------------------------------------------------------------------------------
# Parse Arguments
#------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)              show_help; exit 0 ;;
    -d|--dataset-dir)       dataset_dir="$2"; shift 2 ;;
    -c|--ckpt-dir)          ckpt_dir="$2"; shift 2 ;;
    -n|--num-epochs)        num_epochs="$2"; shift 2 ;;
    -b|--batch-size)        batch_size="$2"; shift 2 ;;
    --lr)                   lr="$2"; shift 2 ;;
    --chunk-size)           chunk_size="$2"; shift 2 ;;
    --with-validation)      with_validation=true; shift ;;
    --with-augmentation)    with_augmentation=true; shift ;;
    --log-type)             log_type="$2"; shift 2 ;;
    --resume)               resume=true; shift ;;
    --hexagon-repo)         hexagon_repo="$2"; shift 2 ;;
    --config-preview)       config_preview=true; shift ;;
    *)                      echo "Unknown option: $1" >&2; show_help; exit 1 ;;
  esac
done

#------------------------------------------------------------------------------
# Validation
#------------------------------------------------------------------------------

[[ -z "$dataset_dir" ]] && { echo "ERROR: --dataset-dir is required" >&2; exit 1; }
[[ -d "$dataset_dir" ]] || { echo "ERROR: Dataset directory not found: $dataset_dir" >&2; exit 1; }

# Resolve hexagon repo
if [[ -z "$hexagon_repo" ]]; then
  for candidate in "${CANDIDATE_PATHS[@]}"; do
    if [[ -d "$candidate" ]]; then
      hexagon_repo="$(cd "$candidate" && pwd)"
      break
    fi
  done
fi

[[ -z "$hexagon_repo" ]] && { echo "ERROR: Cannot locate aeon_il_training_for_microsoft. Use --hexagon-repo." >&2; exit 1; }
[[ -d "$hexagon_repo" ]] || { echo "ERROR: Hexagon repo not found: $hexagon_repo" >&2; exit 1; }
[[ -f "$hexagon_repo/act/imitate_episodes.py" ]] || { echo "ERROR: act/imitate_episodes.py not found in $hexagon_repo" >&2; exit 1; }

# Verify GPU
if command -v nvidia-smi &>/dev/null; then
  echo "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
else
  echo "WARNING: nvidia-smi not found. Training requires a CUDA GPU."
fi

# Resolve to absolute paths
dataset_dir="$(cd "$dataset_dir" && pwd)"
ckpt_dir="$(mkdir -p "$ckpt_dir" && cd "$ckpt_dir" && pwd)"

# Count episodes
episode_count=$(find "$dataset_dir" -name "*.hdf5" | wc -l | tr -d ' ')
[[ "$episode_count" -eq 0 ]] && { echo "ERROR: No HDF5 files found in $dataset_dir" >&2; exit 1; }

#------------------------------------------------------------------------------
# Configuration Preview
#------------------------------------------------------------------------------

echo "========================================"
echo " Hexagon ACT Training Configuration"
echo "========================================"
echo "  Dataset:          $dataset_dir"
echo "  Episodes:         $episode_count"
echo "  Checkpoint Dir:   $ckpt_dir"
echo "  Hexagon Repo:     $hexagon_repo"
echo "  Epochs:           $num_epochs"
echo "  Batch Size:       $batch_size"
echo "  Learning Rate:    $lr"
echo "  Chunk Size:       $chunk_size"
echo "  Validation:       $with_validation"
echo "  Augmentation:     $with_augmentation"
echo "  Log Type:         $log_type"
echo "  Resume:           $resume"
echo "========================================"

if [[ "$config_preview" == "true" ]]; then
  exit 0
fi

#------------------------------------------------------------------------------
# Build Training Command
#------------------------------------------------------------------------------

train_args=(
  -m act.imitate_episodes
  --dataset_path_list "$dataset_dir"
  --ckpt_dir "$ckpt_dir"
  --num_epochs "$num_epochs"
  --batch_size "$batch_size"
  --lr "$lr"
  --chunk_size "$chunk_size"
  --log_type "$log_type"
)

[[ "$with_validation" == "true" ]] && train_args+=(--with_validation)
[[ "$with_augmentation" == "true" ]] && train_args+=(--with_augmentation)
[[ "$resume" == "true" ]] && train_args+=(--resume)

#------------------------------------------------------------------------------
# Run Training
#------------------------------------------------------------------------------

echo ""
echo "Starting training..."
echo "  Command: python3 ${train_args[*]}"
echo ""

cd "$hexagon_repo"
python3 "${train_args[@]}"

#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
echo ""
echo "========================================"
echo " Training Complete"
echo "========================================"
echo "  Checkpoints:   $ckpt_dir"
echo "  Epochs:        $num_epochs"
echo "  Episodes:      $episode_count"
echo "========================================"
