#!/usr/bin/env bash
# Submit pi0-family replay evaluation to Azure ML.
set -o errexit -o nounset

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../../.." && pwd))"

show_help() {
  cat << 'EOF'
Usage: submit-azureml-vla-pi0-eval.sh [OPTIONS] [-- az-ml-job-flags]

Submit pi0 or pi0_fast replay evaluation to Azure ML using the frozen VLA runtime.
All source, dataset, logging, and Azure options are forwarded to
submit-azureml-lerobot-eval.sh.

VLA OPTIONS:
    -p, --policy-type TYPE        Policy architecture: pi0, pi0_fast (default: pi0)

GENERAL:
    -h, --help                    Show this help message
        --config-preview          Print configuration and exit

EXAMPLE:
    submit-azureml-vla-pi0-eval.sh \
      --from-aml-model \
      --model-name vla-pi0 \
      --model-version 1 \
      --from-blob \
      --blob-prefix lerobot/aloha \
      --config-preview
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      show_help
      exit 0
      ;;
  esac
done

policy_type="${POLICY_TYPE:-pi0}"
args=("$@")
for ((index = 0; index < ${#args[@]}; index++)); do
  case "${args[$index]}" in
    -p|--policy-type)
      [[ $((index + 1)) -lt ${#args[@]} ]] || {
        echo "ERROR: ${args[$index]} requires a value" >&2
        exit 1
      }
      policy_type="${args[$((index + 1))]}"
      ;;
  esac
done

case "$policy_type" in
  pi0|pi0_fast) ;;
  *)
    echo "ERROR: Unsupported VLA policy type: $policy_type (use: pi0, pi0_fast)" >&2
    exit 1
    ;;
esac

export LEROBOT_PROJECT="training/vla/lerobot"
export POLICY_TYPE="$policy_type"
export JOB_NAME="${JOB_NAME:-vla-pi0-eval}"

exec "$SCRIPT_DIR/submit-azureml-lerobot-eval.sh" \
  --job-file "$REPO_ROOT/evaluation/sil/workflows/azureml/vla-pi0-eval.yaml" \
  --policy-type "$policy_type" \
  "$@"
