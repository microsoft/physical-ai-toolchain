#!/usr/bin/env bash
#
# Dataset Analysis Tool - Development Server Launcher
# Starts both backend (FastAPI) and frontend (Vite) in the correct order.
#
# Usage:
#   ./start.sh           # Start both services
#   ./start.sh --backend # Start backend only
#   ./start.sh --frontend # Start frontend only
#   ./start.sh --help    # Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || (cd "${SCRIPT_DIR}/../.." && pwd))"
BACKEND_DIR="${SCRIPT_DIR}/backend"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-30}"

# Colors for output
if [[ -n "${NO_COLOR+x}" ]]; then
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
else
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
fi

# PIDs for cleanup
BACKEND_PID=""
FRONTEND_PID=""

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
    cat << EOF
Dataset Analysis Tool - Development Server Launcher

Usage: $(basename "$0") [OPTIONS]

Options:
    --backend             Start backend only
    --frontend            Start frontend only
    --data-dir <path>     Local datasets directory (overrides DATA_DIR env var)
    --check               Check installed launch prerequisites without starting services
    --config-preview      Print configuration and exit without changes
    --help, -h            Show this help message

Environment Variables:
    DATA_DIR        Local datasets directory (default: ../../datasets relative to script)
    BACKEND_PORT    Backend port (default: 8000)
    FRONTEND_PORT   Frontend port (default: 5173)
    HEALTH_TIMEOUT  Seconds to wait for backend health (default: 30)

Examples:
    ./start.sh                                    # Start both services
    ./start.sh --data-dir /path/to/datasets       # Use a specific datasets directory
    DATA_DIR=/path/to/datasets ./start.sh         # Same, via env var
    BACKEND_PORT=9000 ./start.sh                  # Use custom backend port
    ./start.sh --backend                          # Start backend only

EOF
}

cleanup() {
    local status=$?
    trap - EXIT SIGINT SIGTERM SIGHUP
    log_info "Shutting down services..."

    if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
        log_info "Stopping backend (PID: ${BACKEND_PID})"
        kill "${BACKEND_PID}" 2>/dev/null || true
        wait "${BACKEND_PID}" 2>/dev/null || true
    fi

    if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
        log_info "Stopping frontend (PID: ${FRONTEND_PID})"
        kill "${FRONTEND_PID}" 2>/dev/null || true
        wait "${FRONTEND_PID}" 2>/dev/null || true
    fi

    log_success "All services stopped"
    return "${status}"
}

trap cleanup EXIT
trap 'exit 130' SIGINT
trap 'exit 143' SIGTERM
trap 'exit 129' SIGHUP

check_prerequisites() {
    local missing=()

    if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
        missing+=("python3")
    fi

    if ! command -v node &>/dev/null; then
        missing+=("node")
    fi

    if ! command -v npm &>/dev/null; then
        missing+=("npm")
    fi

    if ! command -v curl &>/dev/null; then
        missing+=("curl")
    fi

    if ! command -v uv &>/dev/null; then
        missing+=("uv")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        exit 1
    fi

    for port in "${BACKEND_PORT}" "${FRONTEND_PORT}"; do
        if [[ ! "${port}" =~ ^[0-9]+$ ]] || (( port < 1024 || port > 65535 )); then
            log_error "Ports must be integers from 1024 through 65535"
            exit 1
        fi
    done
    if [[ "${BACKEND_PORT}" == "${FRONTEND_PORT}" ]] || [[ ! "${HEALTH_TIMEOUT}" =~ ^[1-9][0-9]*$ ]]; then
        log_error "Select distinct ports and a positive HEALTH_TIMEOUT"
        exit 1
    fi
}

wait_for_service() {
    local url="$1"
    local pid="$2"
    local label="$3"
    local elapsed=0

    log_info "Waiting for ${label} to be ready..."

    while [[ ${elapsed} -lt ${HEALTH_TIMEOUT} ]]; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            log_error "${label} exited before readiness"
            return 1
        fi
        if curl --max-time 2 -sf "${url}" >/dev/null 2>&1; then
            log_success "${label} is healthy"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    log_error "${label} failed to start within ${HEALTH_TIMEOUT} seconds"
    return 1
}

start_backend() {
    log_info "Starting backend on port ${BACKEND_PORT}..."
    local backend_install_extras=".[dev,analysis,export]"
    local vlm_judge_package_spec="${REPO_ROOT}/evaluation/vlm_judge"
    local should_install_vlm_judge=false

    # Resolve VLM_JUDGE_ENABLED from shell env first, then fall back to backend/.env
    # so that setting it only in .env (the common local-dev pattern) still triggers install.
    if [[ -z "${VLM_JUDGE_ENABLED:-}" ]] && [[ -f "${BACKEND_DIR}/.env" ]]; then
        local env_vlm_enabled
        env_vlm_enabled="$(sed -n 's/^VLM_JUDGE_ENABLED=//p' "${BACKEND_DIR}/.env" | tail -n 1)"
        if [[ -n "${env_vlm_enabled}" ]]; then
            VLM_JUDGE_ENABLED="${env_vlm_enabled}"
        fi
    fi
    # Similarly resolve VLM_JUDGE_BACKEND from .env when not already set.
    if [[ -z "${VLM_JUDGE_BACKEND:-}" ]] && [[ -f "${BACKEND_DIR}/.env" ]]; then
        local env_vlm_backend
        env_vlm_backend="$(sed -n 's/^VLM_JUDGE_BACKEND=//p' "${BACKEND_DIR}/.env" | tail -n 1)"
        if [[ -n "${env_vlm_backend}" ]]; then
            VLM_JUDGE_BACKEND="${env_vlm_backend}"
        fi
    fi

    if [[ "${VLM_JUDGE_ENABLED:-false}" == "true" ]]; then
        should_install_vlm_judge=true
        if [[ "${VLM_JUDGE_BACKEND:-echo}" == "qwen3-vl" ]]; then
            vlm_judge_package_spec="${vlm_judge_package_spec}[qwen3-vl]"
            backend_install_extras=".[dev,analysis,export,vlm-judge]"
        elif [[ "${VLM_JUDGE_BACKEND:-echo}" == "openai-compat" ]]; then
            vlm_judge_package_spec="${vlm_judge_package_spec}[openai]"
        fi
    fi

    if [[ -z "${DATAVIEWER_AUTH_DISABLED:-}" ]]; then
        export DATAVIEWER_AUTH_DISABLED=true
        log_info "Defaulting DATAVIEWER_AUTH_DISABLED=true for local development"
    fi

    # Resolve datasets directory: prefer explicit DATA_DIR, then backend/.env,
    # otherwise default to <repo>/datasets.
    if [[ -z "${DATA_DIR:-}" ]]; then
        if [[ -f "${BACKEND_DIR}/.env" ]]; then
            local env_data_dir
            env_data_dir="$(sed -n 's/^DATA_DIR=//p' "${BACKEND_DIR}/.env" | tail -n 1)"
            if [[ -n "${env_data_dir}" ]]; then
                DATA_DIR="${env_data_dir}"
                log_info "Using DATA_DIR from backend/.env: ${DATA_DIR}"
            fi
        fi
    fi
    if [[ -z "${DATA_DIR:-}" ]]; then
        DATA_DIR="${REPO_ROOT}/datasets"
        log_info "Defaulting DATA_DIR=${DATA_DIR}"
    fi
    if [[ ! -d "${DATA_DIR}" ]]; then
        log_warn "DATA_DIR does not exist: ${DATA_DIR}"
    fi
    export DATA_DIR
    log_info "Using DATA_DIR=${DATA_DIR}"

    if [[ ! -d "${BACKEND_DIR}/.venv" ]]; then
        log_warn "Virtual environment not found at ${BACKEND_DIR}/.venv"
        log_info "Creating virtual environment..."

        if command -v uv &>/dev/null; then
            (cd "${BACKEND_DIR}" && uv venv --python 3.12)
            # shellcheck source=/dev/null
            (cd "${BACKEND_DIR}" && source .venv/bin/activate && uv pip install -e "${backend_install_extras}")
            if [[ "${should_install_vlm_judge}" == "true" ]]; then
                # shellcheck source=/dev/null
                (cd "${BACKEND_DIR}" && source .venv/bin/activate && uv pip install -e "${vlm_judge_package_spec}")
            fi
        else
            log_error "uv not found. Please install uv or create venv manually."
            exit 1
        fi
    elif [[ "${should_install_vlm_judge}" == "true" ]]; then
        log_info "Ensuring VLM judge package dependencies are installed..."
        # shellcheck source=/dev/null
        (cd "${BACKEND_DIR}" && source .venv/bin/activate && uv pip install -e "${backend_install_extras}")
        # shellcheck source=/dev/null
        (cd "${BACKEND_DIR}" && source .venv/bin/activate && uv pip install -e "${vlm_judge_package_spec}")
    fi

    (
        cd "${BACKEND_DIR}"
        # shellcheck source=/dev/null
        source .venv/bin/activate
        exec uvicorn src.api.main:app --reload --host 127.0.0.1 --port "${BACKEND_PORT}" 2>&1
    ) &
    BACKEND_PID=$!

    log_info "Backend started (PID: ${BACKEND_PID})"
}

resolve_vite_bin() {
    local vite_package
    vite_package="$(cd "${FRONTEND_DIR}" && node -p 'require.resolve("vite/package.json")')" || return 1
    printf '%s/bin/vite.js\n' "${vite_package%/*}"
}

start_frontend() {
    log_info "Starting frontend on port ${FRONTEND_PORT}..."
    local frontend_api_base_url="${VITE_API_BASE_URL:-http://localhost:${BACKEND_PORT}}"
    local vite_bin

    if [[ ! -d "${REPO_ROOT}/node_modules" ]]; then
        log_warn "node_modules not found"
        log_info "Installing dependencies..."
        (cd "${REPO_ROOT}" && npm ci)
    fi
    vite_bin="$(resolve_vite_bin)"

    (
        cd "${FRONTEND_DIR}"
        VITE_API_BASE_URL="${frontend_api_base_url}" \
            exec node "${vite_bin}" --host 127.0.0.1 --port "${FRONTEND_PORT}" --strictPort 2>&1
    ) &
    FRONTEND_PID=$!

    log_info "Frontend started (PID: ${FRONTEND_PID})"
}

main() {
    local backend_only=false
    local frontend_only=false
    local check_only=false
    local config_preview=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --backend)
                backend_only=true
                shift
                ;;
            --frontend)
                frontend_only=true
                shift
                ;;
            --check)
                check_only=true
                shift
                ;;
            --data-dir)
                if [[ $# -lt 2 || -z "$2" ]]; then
                    log_error "--data-dir requires a path argument"
                    exit 1
                fi
                DATA_DIR="$2"
                export DATA_DIR
                shift 2
                ;;
            --data-dir=*)
                DATA_DIR="${1#--data-dir=}"
                export DATA_DIR
                shift
                ;;
            --config-preview)
                config_preview=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done

    if [[ "${config_preview}" == "true" ]]; then
        local mode="both"
        if [[ "${backend_only}" == "true" ]]; then
            mode="backend"
        elif [[ "${frontend_only}" == "true" ]]; then
            mode="frontend"
        fi
        log_info "Configuration Preview"
        printf 'Backend Port: %s\n' "${BACKEND_PORT}"
        printf 'Frontend Port: %s\n' "${FRONTEND_PORT}"
        printf 'Data Directory: %s\n' "${DATA_DIR:-${REPO_ROOT}/datasets}"
        printf 'Mode: %s\n' "${mode}"
        printf 'Mutation: None\n'
        return 0
    fi

    check_prerequisites

    if [[ "${check_only}" == "true" ]]; then
        if [[ "${frontend_only}" != "true" && ! -x "${BACKEND_DIR}/.venv/bin/uvicorn" ]]; then
            log_error "Backend environment is missing; run the dataviewer launcher to install it"
            return 1
        fi
        if [[ "${backend_only}" != "true" ]]; then
            local vite_bin
            if ! vite_bin="$(resolve_vite_bin)" || [[ ! -f "${vite_bin}" ]]; then
                log_error "Frontend dependencies are missing; run npm ci in ${REPO_ROOT}"
                return 1
            fi
        fi
        log_success "Launch prerequisites are available; no services started"
        return 0
    fi

    echo ""
    echo "========================================"
    echo "  Dataset Analysis Tool"
    echo "========================================"
    echo ""

    if [[ "${frontend_only}" == "true" ]]; then
        start_frontend
        wait_for_service "http://127.0.0.1:${FRONTEND_PORT}" "${FRONTEND_PID}" Frontend
        log_success "Frontend available at http://localhost:${FRONTEND_PORT}"
        wait "${FRONTEND_PID}"
    elif [[ "${backend_only}" == "true" ]]; then
        start_backend
        wait_for_service "http://127.0.0.1:${BACKEND_PORT}/health" "${BACKEND_PID}" Backend
        log_success "Backend available at http://localhost:${BACKEND_PORT}"
        log_info "API docs: http://localhost:${BACKEND_PORT}/docs"
        wait "${BACKEND_PID}"
    else
        # Start both services
        start_backend

        if wait_for_service "http://127.0.0.1:${BACKEND_PORT}/health" "${BACKEND_PID}" Backend; then
            start_frontend
            wait_for_service "http://127.0.0.1:${FRONTEND_PORT}" "${FRONTEND_PID}" Frontend

            echo ""
            log_success "Both services are running:"
            echo "  - Backend:  http://localhost:${BACKEND_PORT}"
            echo "  - Frontend: http://localhost:${FRONTEND_PORT}"
            echo "  - API Docs: http://localhost:${BACKEND_PORT}/docs"
            echo ""
            log_info "Press Ctrl+C to stop all services"
            echo ""

            # Bash 3.2 on macOS does not support wait -n.
            while kill -0 "${BACKEND_PID}" 2>/dev/null && kill -0 "${FRONTEND_PID}" 2>/dev/null; do
                sleep 1
            done
            log_error "A service exited unexpectedly"
            return 1
        else
            return 1
        fi
    fi
}

main "$@"
