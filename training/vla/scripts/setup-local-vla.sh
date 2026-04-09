#!/usr/bin/env bash
# Set up local TwinVLA development environment with RoboTwin dataset and simulation
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

source "$REPO_ROOT/scripts/lib/common.sh"

# =============================================================================
# Help
# =============================================================================

show_help() {
  cat << 'EOF'
Usage: setup-local-vla.sh [OPTIONS]

Set up a local TwinVLA development environment for bimanual VLA training
and evaluation on a single GPU (RTX 3090/4090/5090 with 24-32 GB VRAM).

Performs the following steps:
  1. Create or reuse a micromamba environment with Python 3.10
  2. Clone and install TwinVLA from source
  3. Download a RoboTwin 2.0 dataset task (RLDS format)
  4. Clone and install RoboTwin simulation for evaluation
  5. Clone and install Tabletop-Sim for ALOHA-style evaluation

OPTIONS:
    -h, --help               Show this help message
    -e, --env-name NAME      Micromamba environment name (default: twinvla)
    -w, --workspace DIR      Working directory for cloned repos (default: ./workspace)
    -d, --dataset-dir DIR    Dataset download directory (default: ./datasets)
    -t, --task NAME          RoboTwin task to download (default: open_laptop)
        --skip-robotwin      Skip RoboTwin simulation setup
        --skip-tabletop      Skip Tabletop-Sim setup
        --skip-dataset       Skip dataset download
        --config-preview     Print configuration and exit

GPU REQUIREMENTS:
    SmolVLM2VLA (256M):  ~16 GB VRAM for LoRA fine-tuning
    Eagle2_1BVLA (1B):   ~24 GB VRAM for LoRA fine-tuning
    Inference + RoboTwin sim: ~8 GB VRAM

EXAMPLES:
    # Standard setup with open_laptop task
    ./setup-local-vla.sh

    # Custom workspace with specific task
    ./setup-local-vla.sh -w /data/vla -t handover_box

    # Training-only setup (skip simulators)
    ./setup-local-vla.sh --skip-robotwin --skip-tabletop
EOF
}

# =============================================================================
# Defaults
# =============================================================================

env_name="twinvla"
workspace_dir="${SCRIPT_DIR}/workspace"
dataset_dir="${SCRIPT_DIR}/datasets"
task_name="open_laptop"
skip_robotwin=false
skip_tabletop=false
skip_dataset=false
config_preview=false

# =============================================================================
# Argument Parsing
# =============================================================================

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)           show_help; exit 0 ;;
    -e|--env-name)       env_name="$2"; shift 2 ;;
    -w|--workspace)      workspace_dir="$2"; shift 2 ;;
    -d|--dataset-dir)    dataset_dir="$2"; shift 2 ;;
    -t|--task)           task_name="$2"; shift 2 ;;
    --skip-robotwin)     skip_robotwin=true; shift ;;
    --skip-tabletop)     skip_tabletop=true; shift ;;
    --skip-dataset)      skip_dataset=true; shift ;;
    --config-preview)    config_preview=true; shift ;;
    *)                   fatal "Unknown option: $1" ;;
  esac
done

require_tools git micromamba

# =============================================================================
# Gather Configuration
# =============================================================================

twinvla_dir="${workspace_dir}/TwinVLA"
robotwin_dir="${workspace_dir}/RoboTwin"
tabletop_dir="${workspace_dir}/Tabletop-Sim"
rlds_dataset_dir="${dataset_dir}/robotwin2_rlds"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Micromamba environment" "$env_name"
  print_kv "Workspace" "$workspace_dir"
  print_kv "Dataset directory" "$dataset_dir"
  print_kv "Task" "$task_name"
  print_kv "TwinVLA" "$twinvla_dir"
  print_kv "RoboTwin sim" "$([[ "$skip_robotwin" == "true" ]] && echo 'Skipped' || echo "$robotwin_dir")"
  print_kv "Tabletop-Sim" "$([[ "$skip_tabletop" == "true" ]] && echo 'Skipped' || echo "$tabletop_dir")"
  print_kv "RLDS dataset" "$([[ "$skip_dataset" == "true" ]] && echo 'Skipped' || echo "$rlds_dataset_dir")"
  exit 0
fi

# =============================================================================
# Step 1: Micromamba Environment
# =============================================================================
section "Micromamba Environment"

if micromamba env list 2>/dev/null | grep -q "${env_name}"; then
  info "Micromamba environment '$env_name' already exists"
else
  info "Creating micromamba environment '$env_name' (Python 3.10)"
  micromamba create -n "$env_name" python=3.10 -c conda-forge -y
fi

info "Activating micromamba environment '$env_name'"
eval "$(micromamba shell hook -s bash)"
micromamba activate "$env_name"

micromamba install -c conda-forge rust -y 2>/dev/null || warn "Rust install skipped (may already be present)"

# =============================================================================
# Step 2: Clone and Install TwinVLA
# =============================================================================
section "TwinVLA Installation"

mkdir -p "$workspace_dir"

if [[ -d "$twinvla_dir" && -f "$twinvla_dir/setup.py" ]]; then
  info "TwinVLA already cloned at $twinvla_dir"
else
  info "Cloning TwinVLA"
  git clone --depth 1 https://github.com/jellyho/TwinVLA.git "$twinvla_dir"
fi

info "Installing TwinVLA requirements"
pip install -r "$twinvla_dir/requirements.txt"
pip install -e "$twinvla_dir"

info "Installing LeRobot and pinning numpy"
pip install "lerobot==0.4.0"
pip install "numpy<2.0.0"

# =============================================================================
# Step 3: Download RoboTwin Dataset
# =============================================================================

if [[ "$skip_dataset" == "false" ]]; then
  section "RoboTwin 2.0 Dataset"
  mkdir -p "$dataset_dir"

  if [[ -d "$rlds_dataset_dir" ]]; then
    info "RLDS dataset already present at $rlds_dataset_dir"
  else
    require_tools huggingface-cli
    info "Downloading RoboTwin 2.0 RLDS dataset (task: $task_name)"
    huggingface-cli download jellyho/robotwin2_rlds \
      --repo-type dataset \
      --local-dir "$rlds_dataset_dir"
  fi
fi

# =============================================================================
# Step 4: RoboTwin Simulation
# =============================================================================

if [[ "$skip_robotwin" == "false" ]]; then
  section "RoboTwin Simulation"

  if [[ -d "$robotwin_dir" ]]; then
    info "RoboTwin already cloned at $robotwin_dir"
  else
    info "Cloning RoboTwin simulation platform"
    git clone --recursive https://github.com/RoboTwin-Platform/RoboTwin.git "$robotwin_dir"
  fi

  info "Installing RoboTwin dependencies"
  pip install -r "$robotwin_dir/script/requirements.txt" 2>/dev/null || warn "Some RoboTwin deps may need manual install"

  info "Linking TwinVLA policy into RoboTwin"
  mkdir -p "$robotwin_dir/policy"
  if [[ ! -d "$robotwin_dir/policy/TwinVLA" ]]; then
    cp -r "$twinvla_dir/TwinVLA_robotwin" "$robotwin_dir/policy/TwinVLA"
  fi
fi

# =============================================================================
# Step 5: Tabletop-Sim
# =============================================================================

if [[ "$skip_tabletop" == "false" ]]; then
  section "Tabletop-Sim"

  if [[ -d "$tabletop_dir" ]]; then
    info "Tabletop-Sim already cloned at $tabletop_dir"
  else
    info "Cloning Tabletop-Sim"
    git clone --recursive https://github.com/jellyho/Tabletop-Sim.git "$tabletop_dir"
  fi

  info "Installing Tabletop-Sim dependencies"
  pip install -r "$tabletop_dir/requirements.txt"
  pip install "numpy<2.0.0"
fi

# =============================================================================
# Summary
# =============================================================================
section "Setup Summary"
print_kv "Micromamba environment" "$env_name"
print_kv "TwinVLA" "$twinvla_dir"
print_kv "RoboTwin sim" "$([[ "$skip_robotwin" == "true" ]] && echo 'Skipped' || echo "$robotwin_dir")"
print_kv "Tabletop-Sim" "$([[ "$skip_tabletop" == "true" ]] && echo 'Skipped' || echo "$tabletop_dir")"
print_kv "RLDS dataset" "$([[ "$skip_dataset" == "true" ]] && echo 'Skipped' || echo "$rlds_dataset_dir")"
info "Activate with: micromamba activate $env_name"
info "Next steps:"
info "  1. Annotate data: npm run dev:backend && npm run dev:frontend (data-management/viewer)"
info "  2. Train: training/vla/scripts/train-local-twinvla.sh -t $task_name"
info "  3. Evaluate: training/vla/scripts/eval-local-twinvla.sh -c <checkpoint> -t $task_name"
