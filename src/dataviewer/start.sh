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
BACKEND_DIR="${SCRIPT_DIR}/backend"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-30}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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
    --backend       Start backend only
    --frontend      Start frontend only
    --help, -h      Show this help message

Environment Variables:
    BACKEND_PORT    Backend port (default: 8000)
    FRONTEND_PORT   Frontend port (default: 5173)
    HEALTH_TIMEOUT  Seconds to wait for backend health (default: 30)

Examples:
    ./start.sh                          # Start both services
    BACKEND_PORT=9000 ./start.sh        # Use custom backend port
    ./start.sh --backend                # Start backend only

EOF
}

cleanup() {
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
    exit 0
}

trap cleanup SIGINT SIGTERM

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
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing prerequisites: ${missing[*]}"
        exit 1
    fi
}

wait_for_backend() {
    local url="http://localhost:${BACKEND_PORT}/health"
    local elapsed=0
    
    log_info "Waiting for backend to be ready..."
    
    while [[ ${elapsed} -lt ${HEALTH_TIMEOUT} ]]; do
        if curl -sf "${url}" >/dev/null 2>&1; then
            log_success "Backend is healthy"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    
    log_error "Backend failed to start within ${HEALTH_TIMEOUT} seconds"
    return 1
}

start_backend() {
    log_info "Starting backend on port ${BACKEND_PORT}..."
    
    if [[ ! -d "${BACKEND_DIR}/.venv" ]]; then
        log_warn "Virtual environment not found at ${BACKEND_DIR}/.venv"
        log_info "Creating virtual environment..."
        
        if command -v uv &>/dev/null; then
            (cd "${BACKEND_DIR}" && uv venv --python 3.11)
            (cd "${BACKEND_DIR}" && source .venv/bin/activate && uv pip install -e ".[dev,analysis,export]")
        else
            log_error "uv not found. Please install uv or create venv manually."
            exit 1
        fi
    fi
    
    (
        cd "${BACKEND_DIR}"
        # shellcheck source=/dev/null
        source .venv/bin/activate
        uvicorn src.api.main:app --reload --port "${BACKEND_PORT}" 2>&1
    ) &
    BACKEND_PID=$!
    
    log_info "Backend started (PID: ${BACKEND_PID})"
}

start_frontend() {
    log_info "Starting frontend on port ${FRONTEND_PORT}..."
    
    if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
        log_warn "node_modules not found"
        log_info "Installing dependencies..."
        (cd "${FRONTEND_DIR}" && npm ci)
    fi

    (
        cd "${FRONTEND_DIR}"
        npm run dev -- --port "${FRONTEND_PORT}" 2>&1
    ) &
    FRONTEND_PID=$!
    
    log_info "Frontend started (PID: ${FRONTEND_PID})"
}

main() {
    local backend_only=false
    local frontend_only=false
    
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
    
    check_prerequisites
    
    echo ""
    echo "========================================"
    echo "  Dataset Analysis Tool"
    echo "========================================"
    echo ""
    
    if [[ "${frontend_only}" == "true" ]]; then
        start_frontend
        log_success "Frontend available at http://localhost:${FRONTEND_PORT}"
        wait "${FRONTEND_PID}"
    elif [[ "${backend_only}" == "true" ]]; then
        start_backend
        if wait_for_backend; then
            log_success "Backend available at http://localhost:${BACKEND_PORT}"
            log_info "API docs: http://localhost:${BACKEND_PORT}/docs"
        fi
        wait "${BACKEND_PID}"
    else
        # Start both services
        start_backend
        
        if wait_for_backend; then
            start_frontend
            
            echo ""
            log_success "Both services are running:"
            echo "  - Backend:  http://localhost:${BACKEND_PORT}"
            echo "  - Frontend: http://localhost:${FRONTEND_PORT}"
            echo "  - API Docs: http://localhost:${BACKEND_PORT}/docs"
            echo ""
            log_info "Press Ctrl+C to stop all services"
            echo ""
            
            # Wait for either process to exit
            wait -n "${BACKEND_PID}" "${FRONTEND_PID}" 2>/dev/null || true
            cleanup
        else
            cleanup
            exit 1
        fi
    fi
}

main "$@"
