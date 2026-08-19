#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore msvc

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

function Get-UvTarget {
    param(
        [string]$Architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture,
        [string]$OperatingSystem = $(if ($IsWindows) { 'windows' } elseif ($IsMacOS) { 'macos' } elseif ($IsLinux) { 'linux' } else { 'unknown' })
    )

    $arch = switch ($Architecture) {
        'X64' { 'x86_64' }
        'Arm64' { 'aarch64' }
        default { throw "Unsupported architecture for uv: $Architecture" }
    }
    $platform = switch ($OperatingSystem) {
        'windows' { 'pc-windows-msvc' }
        'macos' { 'apple-darwin' }
        'linux' { 'unknown-linux-gnu' }
        default { throw "Unsupported operating system for uv: $OperatingSystem" }
    }
    return "$arch-$platform"
}

function Expand-UvArchive {
    param(
        [Parameter(Mandatory)][string]$Archive,
        [Parameter(Mandatory)][string]$DestinationPath,
        [Parameter(Mandatory)][string]$Target
    )

    if ($Target.EndsWith('-pc-windows-msvc')) {
        Expand-Archive -Path $Archive -DestinationPath $DestinationPath -Force
        return $DestinationPath
    }

    tar -xzf $Archive -C $DestinationPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to extract uv archive for $Target"
    }
    return (Join-Path $DestinationPath "uv-$Target")
}

function Install-Uv {
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][hashtable]$Digests,
        [string]$Target = (Get-UvTarget),
        [string]$BinDir = (Join-Path $HOME '.local/bin')
    )

    if (-not $Digests.ContainsKey($Target)) {
        throw "No pinned uv digest for $Target"
    }

    $isWindowsTarget = $Target.EndsWith('-pc-windows-msvc')
    $extension = if ($isWindowsTarget) { 'zip' } else { 'tar.gz' }
    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) "uv-$([guid]::NewGuid())"
    $archive = Join-Path $tempDir "uv.$extension"
    $url = "https://github.com/astral-sh/uv/releases/download/$Version/uv-$Target.$extension"
    try {
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
        $actualHash = (Get-FileHash -Path $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedHash = ([string]$Digests[$Target]).ToLowerInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "uv archive checksum mismatch for ${Target}: expected $expectedHash, got $actualHash"
        }

        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
        $uvExecutable = if ($isWindowsTarget) { 'uv.exe' } else { 'uv' }
        $uvxExecutable = if ($isWindowsTarget) { 'uvx.exe' } else { 'uvx' }
        $extractedDir = Expand-UvArchive -Archive $archive -DestinationPath $tempDir -Target $Target
        $uvSource = Join-Path $extractedDir $uvExecutable
        $uvxSource = Join-Path $extractedDir $uvxExecutable
        if (-not (Test-Path $uvSource) -or -not (Test-Path $uvxSource)) {
            throw "uv archive for $Target does not contain both uv and uvx"
        }
        Move-Item $uvSource (Join-Path $BinDir $uvExecutable) -Force
        Move-Item $uvxSource (Join-Path $BinDir $uvxExecutable) -Force
        $pathEntries = @($env:PATH -split [System.IO.Path]::PathSeparator | Where-Object { $_ })
        $env:PATH = "$BinDir$([System.IO.Path]::PathSeparator)$env:PATH"
        if ($BinDir -notin $pathEntries) {
            Write-Warn "uv was installed to $BinDir. Add this directory to your shell PATH for future terminals."
        }
    }
    finally {
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Get-UvInstallation {
    [CmdletBinding()]
    param()

    $command = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $command) {
        return $null
    }

    $version = ((& $command.Source --version) -split '\s+')[1]
    return [pscustomobject]@{
        Path = $command.Source
        Version = $version
    }
}

function Initialize-Uv {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][hashtable]$Digests
    )

    $installed = Get-UvInstallation
    if (-not $installed -or $installed.Version -ne $Version) {
        if ($installed) {
            Write-Warn "Installing pinned uv $Version ahead of uv $($installed.Version) at $($installed.Path)"
        }
        Write-Info "Installing uv package manager v$Version..."
        Install-Uv -Version $Version -Digests $Digests
    }

    $active = Get-UvInstallation
    if (-not $active) {
        throw "uv not found on PATH after installing pinned version $Version"
    }
    if ($active.Version -ne $Version) {
        throw "Failed to activate pinned uv $Version; found $($active.Version) at $($active.Path)"
    }

    Write-Info "Using uv: uv $($active.Version)"
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

$UvVersion = '0.12.5'
$UvDigests = @{
    'aarch64-apple-darwin'       = '5bb0e5fe008a773c3dbcb97ff79cd89e1241464fe9d2f986d52ad8f1b037bd62'
    'aarch64-pc-windows-msvc'    = '724279317fee6e5fa8ad1908e4eba2bbe764ef1ece5b3f4597927b62b1fe562a'
    'aarch64-unknown-linux-gnu'  = '9bf43b4d1a07665bf64d4c4e710930b382321a785e0eb10aac07f46471f86a31'
    'x86_64-apple-darwin'        = 'b3b2137477cf96c9686ebfb71524614cec780c673fd73e59bce099aef02e70e8'
    'x86_64-pc-windows-msvc'     = '4c4d49d8738847d9b71ba319e49a5688c93eac0fe6204b1df24e98528dddf39a'
    'x86_64-unknown-linux-gnu'   = '68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2'
}

Initialize-Uv -Version $UvVersion -Digests $UvDigests

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
