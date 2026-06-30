#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Development environment setup for physical-ai-toolchain.
.DESCRIPTION
    Verifies required tools, installs uv, sets up Python virtual environment,
    clones Isaac Lab, and checks for hve-core.
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

Write-Section 'Git Symlink Resolution'

# Git symlinks are stored as text files on Windows when core.symlinks=false.
# Replace broken symlinks with junctions (directories) or hard links (files).
$symlinkEntries = git ls-files -s 2>$null | Select-String '120000' | ForEach-Object {
    ($_ -split '\s+', 4)[3]
}
$repairedCount = 0
foreach ($entry in $symlinkEntries) {
    $fullPath = Join-Path $ScriptDir $entry
    if (-not (Test-Path $fullPath)) { continue }

    $item = Get-Item $fullPath -Force
    # Already a junction/symlink — nothing to fix
    if ($item.LinkType) { continue }
    # Only fix plain text files (broken symlink placeholders)
    if ($item.PSIsContainer) { continue }

    $target = (Get-Content $fullPath -Raw).Trim()
    $resolvedTarget = Resolve-Path (Join-Path (Split-Path $fullPath) $target) -ErrorAction SilentlyContinue
    if (-not $resolvedTarget) {
        Write-Warn "Symlink target not found: $entry -> $target"
        continue
    }

    Remove-Item $fullPath -Force
    $targetItem = Get-Item $resolvedTarget.Path
    if ($targetItem.PSIsContainer) {
        New-Item -ItemType Junction -Path $fullPath -Target $resolvedTarget.Path | Out-Null
    }
    else {
        New-Item -ItemType HardLink -Path $fullPath -Target $resolvedTarget.Path | Out-Null
    }
    $repairedCount++
}
if ($repairedCount -gt 0) {
    Write-Info "Repaired $repairedCount broken git symlink(s) (junctions/hard links)"
}
else {
    Write-Info 'All git symlinks are intact'
}

Write-Section 'Tool Verification'

Assert-Tools az, terraform, kubectl, helm, jq
Write-Info 'All required tools found'

Write-Section 'UV Package Manager Setup'

$UvVersion = '0.11.21'

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

$TerraformDocsVersion = '0.24.0'

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

# ===================================================================
# OSV-Scanner
# ===================================================================
Write-Section 'OSV-Scanner Setup'

$OsvScannerVersion = '2.3.8'

$osvInstalled = $null
if (Get-Command osv-scanner -ErrorAction SilentlyContinue) {
    $osvInstalled = (osv-scanner --version 2>&1 | Select-String -Pattern '\d+\.\d+\.\d+' | Select-Object -First 1).Matches.Value
}

if ($osvInstalled -eq $OsvScannerVersion) {
    Write-Info "osv-scanner: v$osvInstalled"
} else {
    Write-Info "Installing osv-scanner v$OsvScannerVersion..."
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $os = if ($IsLinux) { 'linux' } elseif ($IsMacOS) { 'darwin' } else { 'windows' }
    $ext = if ($os -eq 'windows') { '.exe' } else { '' }
    # SHA-256 digests for OSV-Scanner v2.3.8 release assets; keep aligned with setup-dev.sh.
    $OsvScannerDigests = @{
        'linux_amd64'   = 'bc98e15319ed0d515e3f9235287ba53cdc5535d576d24fd573978ecfe9ab92dc'
        'linux_arm64'   = '8158b18edd2d03b1a30d905ca91b032bc62262167be8f206c27114f08823e27c'
        'darwin_amd64'  = 'b8a80a9f14ca4c0cd0fc2d351b28f740da9e6a5b18385ac9f9d083360b5b504e'
        'darwin_arm64'  = 'a8cd6507b06239f463a7642430cfd2d154882f150f6e30cdc0653e28dfc34216'
        'windows_amd64' = 'cb04e79dd9698a7bc821bbfdddec916a416d1409fda79c927c509d37d00c9716'
        'windows_arm64' = '285d1fbcf2c69ab5ee38ae3a850ab46e83f32ef1cd5f3c4c9eb161cc493f6d52'
    }
    $digestKey = "${os}_${arch}"
    $expectedSha = $OsvScannerDigests[$digestKey]
    if (-not $expectedSha) {
        Write-Error "Unsupported OS/arch for osv-scanner: $digestKey"
    }
    $assetName = "osv-scanner_${os}_${arch}${ext}"
    $url = "https://github.com/google/osv-scanner/releases/download/v$OsvScannerVersion/$assetName"
    $dest = Join-Path ([System.IO.Path]::GetTempPath()) "osv-scanner$ext"
    Invoke-WebRequest -Uri $url -OutFile $dest
    $actualSha = (Get-FileHash -Path $dest -Algorithm SHA256).Hash.ToLower()
    if ($actualSha -ne $expectedSha) {
        Remove-Item $dest -ErrorAction SilentlyContinue
        Write-Error "osv-scanner SHA-256 mismatch for ${digestKey}: expected $expectedSha, got $actualSha"
    }
    if ($os -eq 'windows') {
        Move-Item $dest (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\osv-scanner.exe') -Force
    } else {
        sudo install -m 0755 $dest /usr/local/bin/osv-scanner
        Remove-Item $dest -ErrorAction SilentlyContinue
    }
    Write-Info "osv-scanner: v$OsvScannerVersion (installed)"
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

Write-Section 'Isaac Lab Setup'

$IsaacLabDir = Join-Path $ScriptDir 'external' 'IsaacLab'

if (Test-Path $IsaacLabDir) {
    Write-Info "Isaac Lab already cloned at $IsaacLabDir"
    Write-Info "To update, run: cd $IsaacLabDir && git pull"
}
else {
    Write-Info 'Cloning Isaac Lab for intellisense/Pylance support...'
    New-Item -ItemType Directory -Path (Join-Path $ScriptDir 'external') -Force | Out-Null
    git clone 'https://github.com/isaac-sim/IsaacLab.git' $IsaacLabDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "git clone failed (exit code $LASTEXITCODE)"
    }
    Write-Info 'Isaac Lab cloned successfully'
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
