#!/usr/bin/env bash
# GPU-free import smoke for a training/evaluation domain.
# Installs a domain's locked dependencies and imports it to catch syntax,
# import, dependency-resolution, and interpreter/ABI regressions without a GPU.
#
#   cpu   mode: standard runner; CPU torch wheels via `uv --torch-backend cpu`.
#               Catches syntax/import/resolution errors on every PR.
#   image mode: run INSIDE the domain's real runtime container; installs the
#               PR's committed lock exactly as production does and imports the
#               domain on the real interpreter. Catches the interpreter/ABI-at-
#               import class. Expects the repository mounted at the CWD.
set -o errexit -o nounset -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/../.." && pwd))"
# shellcheck source=../../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

# Pinned uv for in-container bootstrap, mirroring training/rl/scripts/train.sh.
UV_VERSION="0.10.9"
UV_SHA256="20d79708222611fa540b5c9ed84f352bcd3937740e51aacc0f8b15b271c57594"

show_help() {
    cat << EOF
Usage: $(basename "$0") DOMAIN [OPTIONS]

Install a domain's locked dependencies and import it, GPU-free.

DOMAIN:
    rl            Reinforcement learning (training/rl), Python 3.11
    il            Imitation learning / LeRobot (training/il/lerobot), Python 3.12
    evaluation    Software-in-the-loop evaluation (evaluation), Python 3.12

OPTIONS:
    -m, --mode MODE    cpu (default) or image
    -h, --help         Show this help message

EXAMPLES:
    $(basename "$0") rl --mode cpu
    $(basename "$0") il --mode image
EOF
}

# Defaults
domain=""
mode="cpu"

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

#------------------------------------------------------------------------------
# Per-domain configuration
#------------------------------------------------------------------------------
# project    Directory holding the domain's pyproject.toml + uv.lock.
# py_version Interpreter the domain targets (matches its requires-python).
# probe      Import probe run AFTER install; non-zero exit fails the smoke.

declare project py_version
declare -a probe

case "$domain" in
    rl)
        project="training/rl"
        py_version="3.11"
        # Import the heavy framework stack (the #809 ABI surface) plus the
        # first-party entrypoint. launch.py defers Isaac/skrl imports, so a bare
        # module import would not exercise the ABI the gate exists to catch.
        probe=(-c "import numpy, torch, skrl; import training.rl.scripts.launch")
        ;;
    il)
        project="training/il/lerobot"
        py_version="3.12"
        # Import lerobot + torch (the #790 interpreter/version surface) plus the
        # first-party module. train.py.main() has side effects (HuggingFace
        # auth), so import the module without executing it.
        probe=(-c "import torch, lerobot; import training.il.scripts.lerobot.train")
        ;;
    evaluation)
        project="evaluation"
        py_version="3.12"
        probe=(-c "import numpy, torch; import evaluation.sil.policy_evaluation")
        ;;
    *) fatal "Unknown domain: $domain (expected rl, il, or evaluation)" ;;
esac

ensure_uv() {
    # Bootstrap a pinned uv inside a container that lacks it (image mode).
    command -v uv &> /dev/null && return 0
    info "Installing uv ${UV_VERSION}"
    curl -LsSf "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/uv.tar.gz
    echo "${UV_SHA256}  /tmp/uv.tar.gz" | sha256sum -c --quiet -
    tar -xzf /tmp/uv.tar.gz -C /tmp
    mkdir -p "${HOME}/.local/bin"
    install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv "${HOME}/.local/bin/uv"
    rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu
    export PATH="${HOME}/.local/bin:${PATH}"
}

#------------------------------------------------------------------------------
# CPU mode: fresh CPU-wheel resolve on a standard runner
#------------------------------------------------------------------------------
smoke_cpu() {
    if [[ "$(uname -s)/$(uname -m)" != "Linux/x86_64" ]]; then
        fatal "CPU smoke installs the linux/x86_64 lock; on this host run it in Docker: shared/ci/smoke-image.sh ${domain} --mode cpu"
    fi
    local venv="/tmp/smoke-venv-${domain}"
    section "CPU import smoke: ${domain}"
    uv venv --clear --python "$py_version" "$venv"
    export VIRTUAL_ENV="$venv"
    export PATH="${venv}/bin:${PATH}"

    # Install the exact committed lock with --no-deps -- re-resolving would
    # discard the pyproject override-dependencies the lock encodes and fail.
    # Strip the CUDA runtime wheels (CPU torch needs none); --torch-backend cpu
    # redirects torch to CPU wheels. pipefail fails the step on a bad export.
    uv export --frozen --no-hashes --no-emit-project --project "$project" \
        | grep -vE '^(nvidia-|cuda-)' \
        | uv pip install --torch-backend cpu --no-cache-dir --no-deps --requirement -

    run_probe "${venv}/bin/python"
}

#------------------------------------------------------------------------------
# Image mode: production lock install on the real interpreter, in-container
#------------------------------------------------------------------------------
smoke_image() {
    section "Runtime-image import smoke: ${domain}"

    local python_exec
    local -a install_args
    if [[ "$domain" == "il" ]]; then
        # Published PyTorch images ship Python 3.11; LeRobot needs >= 3.12.
        # Provision 3.12 in a venv, exactly as the production entry script does.
        local venv="/tmp/smoke-venv-il"
        uv python install "$py_version"
        uv venv --clear --python "$py_version" "$venv"
        export VIRTUAL_ENV="$venv"
        export PATH="${venv}/bin:${PATH}"
        python_exec="${venv}/bin/python"
        install_args=(--no-cache-dir --no-deps --requirement -)
    else
        # RL / evaluation: the Isaac Lab kit interpreter is the production runtime.
        python_exec="/isaac-sim/kit/python/bin/python3"
        [[ -x "$python_exec" ]] || python_exec="python3"
        export UV_PYTHON="$python_exec"
        install_args=(--no-cache-dir --no-deps --system --requirement -)
    fi

    # Mirror production (training/rl/scripts/train.sh): install the committed lock
    # with --no-deps onto the real interpreter; pipefail fails on a bad export.
    uv export --frozen --no-hashes --no-emit-project --project "$project" \
        | uv pip install "${install_args[@]}"

    run_probe "$python_exec"
}

run_probe() {
    local python_exec="$1"
    info "Probe: ${python_exec} ${probe[*]}"
    PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" "$python_exec" "${probe[@]}"
}

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------
cd "$REPO_ROOT"

# Bootstrap uv when absent (in-container image mode); no-op when setup-uv
# already provided it (CPU mode on the runner).
ensure_uv

if [[ "$mode" == "cpu" ]]; then
    smoke_cpu
else
    smoke_image
fi

section "Smoke Summary"
print_kv "Domain" "$domain"
print_kv "Mode" "$mode"
print_kv "Project" "$project"
print_kv "Python" "$py_version"
info "Import smoke passed"
