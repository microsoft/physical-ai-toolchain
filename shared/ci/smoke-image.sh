#!/usr/bin/env bash
# Run a domain's import smoke inside a linux/amd64 container, against the
# repository mounted at /workspace. Lets any host (including non-linux) run the
# smoke through Docker. Shared by CI and local runs.
#   --mode image (default): the domain's production runtime container.
#   --mode cpu:             a lightweight uv container (CPU torch wheels).
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Source of truth for the LeRobot runtime image (the default-values block).
LEROBOT_WORKFLOW="training/il/workflows/osmo/lerobot-train.yaml"
# Source of truth for the Azure ML evaluation runtime image.
EVALUATION_COMPONENT="evaluation/sil/workflows/azureml/components/evaluate.yaml"
# Source of truth for the OSMO replay runtime image.
OSMO_REPLAY_WORKFLOW="workflows/osmo/replay-azureml.yaml"
# Lightweight linux/amd64 image with uv preinstalled, used for the CPU smoke.
CPU_IMAGE="ghcr.io/astral-sh/uv:python3.12-bookworm-slim"

show_help() {
    cat << EOF
Usage: $(basename "$0") DOMAIN [--mode cpu|image]

Run a domain's import smoke inside a linux/amd64 container, with the repository
mounted at /workspace. Requires Docker.

DOMAIN:
    rl            Reinforcement learning (training/rl)
    il            Imitation learning / LeRobot (training/il/lerobot)
    vla           Vision-language-action / LeRobot (training/vla/lerobot)
    evaluation    Software-in-the-loop evaluation (evaluation)
    vlm-judge     VLM-as-judge optional runtime (evaluation/vlm_judge) -- --mode cpu only
    osmo-replay   OSMO-to-AzureML replay mirror (workflows/osmo)

OPTIONS:
    -m, --mode MODE    image (default) runs the domain's production container;
                       cpu runs a lightweight uv container (CPU torch wheels)
    -h, --help         Show this help message

EXAMPLES:
    $(basename "$0") rl                 # runtime-image smoke (Isaac Lab)
    $(basename "$0") il --mode cpu      # CPU import smoke in Docker
    $(basename "$0") evaluation         # runtime-image smoke (PyTorch)
EOF
}

domain=""
mode="image"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h | --help) show_help; exit 0 ;;
        -m | --mode) mode="$2"; shift 2 ;;
        -*) fatal "Unknown option: $1" ;;
        *)
            [[ -z "$domain" ]] || fatal "Unexpected argument: $1"
            domain="$1"
            shift
            ;;
    esac
done

[[ -n "$domain" ]] || { show_help; fatal "DOMAIN is required"; }
[[ "$mode" == "cpu" || "$mode" == "image" ]] || fatal "Invalid mode: $mode (expected cpu or image)"

if [[ "$mode" == "cpu" ]]; then
    case "$domain" in
        rl | il | vla | evaluation | vlm-judge | osmo-replay) image="$CPU_IMAGE" ;;
        *) fatal "Unknown domain: $domain (expected rl, il, vla, evaluation, vlm-judge, or osmo-replay)" ;;
    esac
else
    case "$domain" in
        rl) image="$DEFAULT_ISAAC_LAB_IMAGE" ;;
        il)
            image="$(grep -m1 -E '^[[:space:]]*image:[[:space:]]*pytorch/' \
                "$REPO_ROOT/$LEROBOT_WORKFLOW" | awk '{print $2}')"
            [[ -n "$image" ]] || fatal "Could not resolve LeRobot image from $LEROBOT_WORKFLOW"
            ;;
        evaluation)
            image="$(grep -m1 -E '^[[:space:]]*image:[[:space:]]*pytorch/' \
                "$REPO_ROOT/$EVALUATION_COMPONENT" | awk '{print $2}')"
            [[ -n "$image" ]] || fatal "Could not resolve evaluation image from $EVALUATION_COMPONENT"
            ;;
        osmo-replay)
            image="$(grep -m1 -E '^[[:space:]]*image:[[:space:]]*python:' \
                "$REPO_ROOT/$OSMO_REPLAY_WORKFLOW" | awk '{print $2}')"
            [[ -n "$image" ]] || fatal "Could not resolve OSMO replay image from $OSMO_REPLAY_WORKFLOW"
            ;;
        vla) image="$DEFAULT_LEROBOT_TRAIN_IMAGE" ;;
        vlm-judge) fatal "vlm-judge has no runtime-image smoke; use --mode cpu" ;;
        *) fatal "Unknown domain: $domain (expected rl, il, vla, evaluation, vlm-judge, or osmo-replay)" ;;
    esac
fi

require_tools docker

section "Smoke in container: ${domain} (${mode})"
print_kv "Image" "$image"
print_kv "Mount" "${REPO_ROOT} -> /workspace"

# The CPU image is lightweight and lacks the build toolchain that ubuntu-latest
# provides in CI; install it so C-extension sdists (e.g. evdev) compile locally.
container_cmd="shared/ci/smoke-import.sh ${domain} --mode ${mode}"
if [[ "$mode" == "cpu" ]]; then
    container_cmd="apt-get update -qq \
        && apt-get install -y -qq --no-install-recommends build-essential linux-libc-dev >/dev/null \
        && ${container_cmd}"
elif [[ "$domain" == "osmo-replay" ]]; then
    container_cmd="apt-get update -qq \
        && apt-get install -y -qq --no-install-recommends ca-certificates curl >/dev/null \
        && ${container_cmd}"
fi

docker_args=(run --rm --platform linux/amd64 --entrypoint bash)
package_index_url="${UV_INDEX_URL:-${UV_DEFAULT_INDEX:-}}"
if [[ -n "$package_index_url" ]]; then
    export UV_INDEX_URL="$package_index_url"
    export PIP_INDEX_URL="$package_index_url"
    docker_args+=(-e UV_INDEX_URL -e PIP_INDEX_URL)
fi

docker "${docker_args[@]}" \
    -v "${REPO_ROOT}:/workspace" -w /workspace \
    "$image" -c "$container_cmd"
