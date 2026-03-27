#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
DISABLE_VENV=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --disable-venv)
      DISABLE_VENV=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# shellcheck source=shared/lib/common.sh
source "${SCRIPT_DIR}/shared/lib/common.sh"

# Preamble: Recommend devcontainer for easier setup
echo
echo "💡 RECOMMENDED: Use the Dev Container for the best experience."
echo
echo "The devcontainer includes all tools pre-configured:"
echo "  • Azure CLI, Terraform, kubectl, helm, jq"
echo "  • Python with all dependencies"
echo "  • VS Code extensions for Terraform and Python"
echo
echo "To use:"
echo "  VS Code    → Reopen in Container (F1 → Dev Containers: Reopen)"
echo "  Codespaces → Open in Codespace from GitHub"
echo
echo "If this script fails, the devcontainer is your fallback."
echo

section "Tool Verification"

require_tools az terraform kubectl helm jq
info "All required tools found"

section "UV Package Manager Setup"

if ! command -v uv &>/dev/null; then
  info "Installing uv package manager..."
  curl -LsSf https://astral.sh/uv/0.10.9/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

info "Using uv: $(uv --version)"

# ===================================================================
# Terraform-Docs
# ===================================================================
section "Terraform-Docs Setup"

TERRAFORM_DOCS_VERSION="0.21.0"

ARCH=$(uname -m)
case "${ARCH}" in
  x86_64)  ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) error "Unsupported architecture: ${ARCH}"; exit 1 ;;
esac

if command -v terraform-docs &>/dev/null; then
  info "terraform-docs: $(terraform-docs --version)"
else
  info "Installing terraform-docs v${TERRAFORM_DOCS_VERSION}..."
  curl -sSLo /tmp/terraform-docs.tar.gz \
    "https://github.com/terraform-docs/terraform-docs/releases/download/v${TERRAFORM_DOCS_VERSION}/terraform-docs-v${TERRAFORM_DOCS_VERSION}-$(uname -s | tr '[:upper:]' '[:lower:]')-${ARCH}.tar.gz"
  tar -xzf /tmp/terraform-docs.tar.gz -C /tmp terraform-docs
  sudo mv /tmp/terraform-docs /usr/local/bin/terraform-docs
  sudo chmod +x /usr/local/bin/terraform-docs
  rm -f /tmp/terraform-docs.tar.gz
  info "terraform-docs: v${TERRAFORM_DOCS_VERSION} (installed)"
fi

section "Python Environment Setup"

PYTHON_VERSION="$(cat "${SCRIPT_DIR}/.python-version")"
info "Target Python version: ${PYTHON_VERSION}"

if [[ "${DISABLE_VENV}" == "true" ]]; then
  info "Virtual environment disabled, installing packages directly..."
else
  if [[ ! -d "${VENV_DIR}" ]]; then
    info "Creating virtual environment at ${VENV_DIR} with Python ${PYTHON_VERSION}..."
    uv venv "${VENV_DIR}" --python "${PYTHON_VERSION}"
  else
    info "Virtual environment already exists at ${VENV_DIR}"
  fi
fi

info "Syncing dependencies from pyproject.toml..."
uv sync

info "Locking dependencies..."
uv lock

section "IsaacLab Setup"

ISAACLAB_DIR="${SCRIPT_DIR}/external/IsaacLab"

if [[ -d "${ISAACLAB_DIR}" ]]; then
  info "IsaacLab already cloned at ${ISAACLAB_DIR}"
  info "To update, run: cd ${ISAACLAB_DIR} && git pull"
else
  info "Cloning IsaacLab for intellisense/Pylance support..."
  mkdir -p "${SCRIPT_DIR}/external"
  git clone https://github.com/isaac-sim/IsaacLab.git "${ISAACLAB_DIR}"
  info "IsaacLab cloned successfully"
fi

section "hve-core Check"

if [[ ! -d "${SCRIPT_DIR}/../hve-core" ]]; then
  warn "hve-core not found at ${SCRIPT_DIR}/../hve-core"
  warn "Install for Copilot workflows: https://github.com/microsoft/hve-core/blob/main/docs/getting-started/install.md"
  warn "Or install the VS Code Extension: ise-hve-essentials.hve-core"
else
  info "hve-core found at ${SCRIPT_DIR}/../hve-core"
fi

section "Setup Complete"

echo
echo "✅ Development environment setup complete!"
echo
if [[ "${DISABLE_VENV}" == "false" ]]; then
  warn "Run this command to activate the virtual environment:"
  echo
  echo "  source .venv/bin/activate"
  echo
fi
echo "Next steps:"
echo "  1. Run: source infrastructure/terraform/prerequisites/az-sub-init.sh"
echo "  2. Configure: infrastructure/terraform/terraform.tfvars"
echo "  3. Deploy: cd infrastructure/terraform && terraform init && terraform apply"
echo
echo "Documentation:"
echo "  - README.md           - Quick start guide"
echo "  - docs/infrastructure/README.md - Deployment overview"
echo
