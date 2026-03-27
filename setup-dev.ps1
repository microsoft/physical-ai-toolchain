#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Development environment setup for physical-ai-toolchain.
.DESCRIPTION
    Verifies required tools, installs uv, sets up Python virtual environment,
    clones IsaacLab, and checks for hve-core.
.PARAMETER DisableVenv
    Skip virtual environment creation; install packages directly.
.EXAMPLE
    ./setup-dev.ps1
.EXAMPLE
    ./setup-dev.ps1 -DisableVenv
#>

[CmdletBinding()]
param(
    [switch]$DisableVenv
)

$ErrorActionPreference = 'Stop'

#region Helper Functions

function Write-Info {
    param([string]$Message)
    if ($env:NO_COLOR) {
        Write-Host "[INFO]  $Message"
    }
    else {
        Write-Host "[INFO]  $Message" -ForegroundColor Blue
    }
}

function Write-Warn {
    param([string]$Message)
    if ($env:NO_COLOR) {
        Write-Warning "[WARN]  $Message"
    }
    else {
        Write-Host "[WARN]  $Message" -ForegroundColor Yellow
    }
}

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host '============================'
    Write-Host $Title
    Write-Host '============================'
}

function Assert-Tools {
    param([string[]]$Tools)
    $missing = @()
    foreach ($tool in $Tools) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            $missing += $tool
        }
    }
    if ($missing.Count -gt 0) {
        Write-Error "Missing required tools: $($missing -join ', ')"
    }
}

#endregion

$ScriptDir = $PSScriptRoot
$VenvDir = Join-Path $ScriptDir '.venv'

# Devcontainer recommendation
Write-Host ''
Write-Host ([char]0x1F4A1 + ' RECOMMENDED: Use the Dev Container for the best experience.')
Write-Host ''
Write-Host 'The devcontainer includes all tools pre-configured:'
Write-Host '  - Azure CLI, Terraform, kubectl, helm, jq'
Write-Host '  - Python with all dependencies'
Write-Host '  - VS Code extensions for Terraform and Python'
Write-Host ''
Write-Host 'To use:'
Write-Host '  VS Code    -> Reopen in Container (F1 -> Dev Containers: Reopen)'
Write-Host '  Codespaces -> Open in Codespace from GitHub'
Write-Host ''
Write-Host 'If this script fails, the devcontainer is your fallback.'
Write-Host ''

Write-Section 'Tool Verification'

Assert-Tools az, terraform, kubectl, helm, jq
Write-Info 'All required tools found'

Write-Section 'UV Package Manager Setup'

$UvVersion = '0.7.12'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Info "Installing uv package manager v$UvVersion..."
    $uvInstaller = Join-Path ([System.IO.Path]::GetTempPath()) 'uv-install.ps1'
    try {
        Invoke-WebRequest -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $uvInstaller -UseBasicParsing
        & $uvInstaller
    }
    finally {
        Remove-Item $uvInstaller -Force -ErrorAction SilentlyContinue
    }
    if (-not $IsWindows) {
        $env:PATH = "$HOME/.local/bin:$HOME/.cargo/bin:$env:PATH"
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install uv v$UvVersion"
    }
}

Write-Info "Using uv: $(uv --version)"

# ===================================================================
# Terraform-Docs
# ===================================================================
Write-Section 'Terraform-Docs Setup'

$TerraformDocsVersion = '0.21.0'

if (Get-Command terraform-docs -ErrorAction SilentlyContinue) {
    Write-Info "terraform-docs: $(terraform-docs --version)"
} else {
    Write-Info "Installing terraform-docs v$TerraformDocsVersion..."
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $os = if ($IsLinux) { 'linux' } elseif ($IsMacOS) { 'darwin' } else { 'windows' }
    $ext = if ($os -eq 'windows') { 'zip' } else { 'tar.gz' }
    $url = "https://github.com/terraform-docs/terraform-docs/releases/download/v$TerraformDocsVersion/terraform-docs-v$TerraformDocsVersion-$os-$arch.$ext"
    $dest = Join-Path $env:TEMP "terraform-docs.$ext"
    Invoke-WebRequest -Uri $url -OutFile $dest
    if ($os -eq 'windows') {
        Expand-Archive -Path $dest -DestinationPath $env:TEMP -Force
        Move-Item (Join-Path $env:TEMP 'terraform-docs.exe') (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\terraform-docs.exe') -Force
    } else {
        tar -xzf $dest -C /tmp terraform-docs
        sudo mv /tmp/terraform-docs /usr/local/bin/terraform-docs
        sudo chmod +x /usr/local/bin/terraform-docs
    }
    Remove-Item $dest -ErrorAction SilentlyContinue
    Write-Info "terraform-docs: v$TerraformDocsVersion (installed)"
}

Write-Section 'Python Environment Setup'

$PythonVersion = Get-Content (Join-Path $ScriptDir '.python-version') -Raw
$PythonVersion = $PythonVersion.Trim()
Write-Info "Target Python version: $PythonVersion"

if ($DisableVenv) {
    Write-Info 'Virtual environment disabled, installing packages directly...'
}
else {
    if (-not (Test-Path $VenvDir)) {
        Write-Info "Creating virtual environment at $VenvDir with Python $PythonVersion..."
        uv venv $VenvDir --python $PythonVersion
        if ($LASTEXITCODE -ne 0) {
            Write-Error "uv venv failed (exit code $LASTEXITCODE)"
        }
    }
    else {
        Write-Info "Virtual environment already exists at $VenvDir"
    }
}

Write-Info 'Syncing dependencies from pyproject.toml...'
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv sync failed (exit code $LASTEXITCODE)"
}

Write-Info 'Locking dependencies...'
uv lock
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv lock failed (exit code $LASTEXITCODE)"
}

Write-Section 'IsaacLab Setup'

$IsaacLabDir = Join-Path $ScriptDir 'external' 'IsaacLab'

if (Test-Path $IsaacLabDir) {
    Write-Info "IsaacLab already cloned at $IsaacLabDir"
    Write-Info "To update, run: cd $IsaacLabDir && git pull"
}
else {
    Write-Info 'Cloning IsaacLab for intellisense/Pylance support...'
    New-Item -ItemType Directory -Path (Join-Path $ScriptDir 'external') -Force | Out-Null
    git clone 'https://github.com/isaac-sim/IsaacLab.git' $IsaacLabDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "git clone failed (exit code $LASTEXITCODE)"
    }
    Write-Info 'IsaacLab cloned successfully'
}

Write-Section 'hve-core Check'

$HveCoreDir = Join-Path $ScriptDir '..' 'hve-core'
if (-not (Test-Path $HveCoreDir)) {
    Write-Warn "hve-core not found at $HveCoreDir"
    Write-Warn 'Install for Copilot workflows: https://github.com/microsoft/hve-core/blob/main/docs/getting-started/install.md'
    Write-Warn 'Or install the VS Code Extension: ise-hve-essentials.hve-core'
}
else {
    Write-Info "hve-core found at $HveCoreDir"
}

Write-Section 'Setup Complete'

Write-Host ''
Write-Host 'Development environment setup complete!'
Write-Host ''
if (-not $DisableVenv) {
    Write-Warn 'Run this command to activate the virtual environment:'
    Write-Host ''
    if ($IsWindows) {
        Write-Host '  .venv\Scripts\Activate.ps1'
    }
    else {
        Write-Host '  source .venv/bin/activate'
    }
    Write-Host ''
}
Write-Host 'Next steps:'
Write-Host '  1. Run: . infrastructure/terraform/prerequisites/az-sub-init.ps1'
Write-Host '  2. Configure: infrastructure/terraform/terraform.tfvars'
Write-Host '  3. Deploy: cd infrastructure/terraform && terraform init && terraform apply'
Write-Host ''
Write-Host 'Documentation:'
Write-Host '  - README.md           - Quick start guide'
Write-Host '  - infrastructure/README.md    - Deployment overview'
Write-Host ''
