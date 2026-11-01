#!/usr/bin/env bash
# Manage LeRobot datasets in the local data root: list, import, derive learner datasets, and remove.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"
# shellcheck source=../../scripts/lib/platform-profile.sh
source "$REPO_ROOT/scripts/lib/platform-profile.sh"

TOOL="$REPO_ROOT/data-management/tools/lerobot_datasets.py"
LEROBOT_PROJECT="$REPO_ROOT/training/il/lerobot"

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS] COMMAND [ARGS...]

Manage the LeRobot datasets that local OSMO jobs mount read-only at /mnt/datasets and the
dataviewer browses. Every file is written as you, in the local data root's datasets directory.

COMMANDS:
    list                              List datasets
    import SOURCE NAME                Copy a LeRobot dataset (or a capture directory with
                                      lerobot/) in as NAME
    derive NAME --source DIR...       Merge the passing, successful episodes of each source into
           [--drop-feature F...]      NAME without the dropped features; sources are read, never
           [--include-unvalidated]    changed. Runs lerobot-edit-dataset from the LeRobot lock.
    remove NAME [--yes]               Delete a dataset after confirmation

Sources qualify through their sidecars: export-validation.json must pass, ledger.json episodes
must have passed, and meta/episode_labels.json episodes must be labeled SUCCESS.

OPTIONS:
    -h, --help               Show this help message
    --config-preview         Print configuration and exit

EXAMPLES:
    $(basename "$0") list
    $(basename "$0") derive so101-learner \\
      --source <capture-root>/so101-cube-box-overhead \\
      --source <capture-root>/so101-assisted-pick-place-retry1 \\
      --drop-feature observation.environment_state \\
      --drop-feature observation.task_phase \\
      --drop-feature observation.assistance
    $(basename "$0") remove so101-learner
EOF
}

config_preview=false
tool_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)          show_help; exit 0 ;;
    --config-preview)   config_preview=true; shift ;;
    *)                  tool_args=("$@"); break ;;
  esac
done

#------------------------------------------------------------------------------
# Gather Configuration
#------------------------------------------------------------------------------

load_platform_profile local
datasets_dir="$PROFILE_DATASETS_HOST_DIR"

if [[ "$config_preview" == "true" ]]; then
  section "Configuration Preview"
  print_kv "Datasets Directory" "$datasets_dir"
  print_kv "Job Mount" "$PROFILE_DATASETS_JOB_DIR (read-only)"
  print_kv "LeRobot Project" "$LEROBOT_PROJECT"
  exit 0
fi

[[ ${#tool_args[@]} -gt 0 ]] || { show_help; fatal "A command is required"; }
[[ -d "$datasets_dir" ]] || fatal "Datasets directory $datasets_dir is missing; run infrastructure/local/00-prepare-host.sh"

#------------------------------------------------------------------------------
# Run
#------------------------------------------------------------------------------

if [[ "${tool_args[0]}" == "derive" ]]; then
  # Derivation edits datasets with the same LeRobot release the training jobs install.
  require_tools uv
  info "Running in the LeRobot environment from $LEROBOT_PROJECT/uv.lock (first use downloads it)"
  exec uv run --project "$LEROBOT_PROJECT" --frozen python "$TOOL" --datasets-dir "$datasets_dir" "${tool_args[@]}"
fi

require_tools python3
exec python3 "$TOOL" --datasets-dir "$datasets_dir" "${tool_args[@]}"
