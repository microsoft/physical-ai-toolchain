#!/usr/bin/env bash
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

# shellcheck source=scripts/lib/common.sh
source "${SCRIPT_DIR}/scripts/lib/common.sh"

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

# Portable SHA-256 verifier: GNU sha256sum on Linux/devcontainer; shasum on macOS where coreutils may be absent.
verify_sha256() {
  local expected="$1"
  local file="$2"
  if command -v sha256sum &>/dev/null; then
    echo "${expected}  ${file}" | sha256sum -c --quiet -
  elif command -v shasum &>/dev/null; then
    echo "${expected}  ${file}" | shasum -a 256 -c --quiet -
  else
    error "Neither sha256sum nor shasum available; cannot verify ${file}"
    exit 1
  fi
}

section "UV Package Manager Setup"

if ! command -v uv &>/dev/null; then
  info "Installing uv package manager..."
  UV_VERSION="0.7.12"
  UV_ARCH=$(uname -m)
  case "${UV_ARCH}" in
    x86_64)  UV_TRIPLE="x86_64-unknown-linux-gnu"; UV_SHA256="735891fb553d0be129f3aa39dc8e9c4c49aaa76ec17f7dfb6a732e79a714873a" ;;
    aarch64) UV_TRIPLE="aarch64-unknown-linux-gnu"; UV_SHA256="23233d2e950ed8187858350da5c6803b14cbbeaef780382093bb2f2bc4ba1200" ;;
    *) error "Unsupported architecture for uv: ${UV_ARCH}"; exit 1 ;;
  esac
  curl -LsSf "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${UV_TRIPLE}.tar.gz" -o /tmp/uv.tar.gz
  verify_sha256 "${UV_SHA256}" /tmp/uv.tar.gz
  tar -xzf /tmp/uv.tar.gz -C /tmp
  sudo install -m 0755 "/tmp/uv-${UV_TRIPLE}/uv" /usr/local/bin/uv
  sudo install -m 0755 "/tmp/uv-${UV_TRIPLE}/uvx" /usr/local/bin/uvx
  rm -rf /tmp/uv.tar.gz "/tmp/uv-${UV_TRIPLE}"
fi

info "Using uv: $(uv --version)"

# ===================================================================
# Terraform-Docs
# ===================================================================
section "Terraform-Docs Setup"

TERRAFORM_DOCS_VERSION="0.21.0"

ARCH=$(uname -m)
case "${ARCH}" in
  x86_64)  ARCH="amd64"; TERRAFORM_DOCS_SHA256="2fdd81b8d21ff1498cd559af0dcc5d155835f84600db06d3923e217124fc735a" ;;
  aarch64|arm64) ARCH="arm64"; TERRAFORM_DOCS_SHA256="35b2e6846268841484e6eea7d00d7dfe2c94b4725e52cfe19aa6c26a86c32edc" ;;
  *) error "Unsupported architecture: ${ARCH}"; exit 1 ;;
esac

if command -v terraform-docs &>/dev/null; then
  info "terraform-docs: $(terraform-docs --version)"
else
  info "Installing terraform-docs v${TERRAFORM_DOCS_VERSION}..."
  curl -sSLo /tmp/terraform-docs.tar.gz \
    "https://github.com/terraform-docs/terraform-docs/releases/download/v${TERRAFORM_DOCS_VERSION}/terraform-docs-v${TERRAFORM_DOCS_VERSION}-$(uname -s | tr '[:upper:]' '[:lower:]')-${ARCH}.tar.gz"
  verify_sha256 "${TERRAFORM_DOCS_SHA256}" /tmp/terraform-docs.tar.gz
  tar -xzf /tmp/terraform-docs.tar.gz -C /tmp terraform-docs
  sudo mv /tmp/terraform-docs /usr/local/bin/terraform-docs
  sudo chmod +x /usr/local/bin/terraform-docs
  rm -f /tmp/terraform-docs.tar.gz
  info "terraform-docs: v${TERRAFORM_DOCS_VERSION} (installed)"
fi

# ===================================================================
# OSV-Scanner
# ===================================================================
section "OSV-Scanner Setup"

OSV_SCANNER_VERSION="2.3.8"
OSV_OS=$(uname -s | tr '[:upper:]' '[:lower:]')
OSV_ARCH_RAW=$(uname -m)
case "${OSV_ARCH_RAW}" in
  x86_64)        OSV_ARCH="amd64" ;;
  aarch64|arm64) OSV_ARCH="arm64" ;;
  *) error "Unsupported architecture for osv-scanner: ${OSV_ARCH_RAW}"; exit 1 ;;
esac

case "${OSV_OS}_${OSV_ARCH}" in
  linux_amd64)  OSV_SCANNER_SHA256="bc98e15319ed0d515e3f9235287ba53cdc5535d576d24fd573978ecfe9ab92dc" ;;
  linux_arm64)  OSV_SCANNER_SHA256="8158b18edd2d03b1a30d905ca91b032bc62262167be8f206c27114f08823e27c" ;;
  darwin_amd64) OSV_SCANNER_SHA256="b8a80a9f14ca4c0cd0fc2d351b28f740da9e6a5b18385ac9f9d083360b5b504e" ;;
  darwin_arm64) OSV_SCANNER_SHA256="a8cd6507b06239f463a7642430cfd2d154882f150f6e30cdc0653e28dfc34216" ;;
  *) error "Unsupported OS/arch for osv-scanner: ${OSV_OS}/${OSV_ARCH}"; exit 1 ;;
esac

OSV_INSTALLED_VERSION=""
if command -v osv-scanner &>/dev/null; then
  OSV_INSTALLED_VERSION=$(osv-scanner --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)
fi

if [[ "${OSV_INSTALLED_VERSION}" == "${OSV_SCANNER_VERSION}" ]]; then
  info "osv-scanner: v${OSV_INSTALLED_VERSION}"
else
  info "Installing osv-scanner v${OSV_SCANNER_VERSION}..."
  curl -sSLo /tmp/osv-scanner \
    "https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_${OSV_OS}_${OSV_ARCH}"
  verify_sha256 "${OSV_SCANNER_SHA256}" /tmp/osv-scanner
  sudo install -m 0755 /tmp/osv-scanner /usr/local/bin/osv-scanner
  rm -f /tmp/osv-scanner
  info "osv-scanner: v${OSV_SCANNER_VERSION} (installed)"
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

section "Isaac Lab Setup"

ISAACLAB_DIR="${SCRIPT_DIR}/external/IsaacLab"

if [[ -d "${ISAACLAB_DIR}" ]]; then
  info "Isaac Lab already cloned at ${ISAACLAB_DIR}"
  info "To update, run: cd ${ISAACLAB_DIR} && git pull"
else
  info "Cloning Isaac Lab for intellisense/Pylance support..."
  mkdir -p "${SCRIPT_DIR}/external"
  git clone https://github.com/isaac-sim/IsaacLab.git "${ISAACLAB_DIR}"
  info "Isaac Lab cloned successfully"
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
