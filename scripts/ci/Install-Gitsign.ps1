#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Installs a pinned gitsign binary for release tag signing.
.PARAMETER Version
    gitsign release tag to install.
.PARAMETER RunnerArch
    GitHub Actions runner architecture.
.PARAMETER RunnerTemp
    Temporary directory for downloads and installation staging.
#>
[CmdletBinding()]
param(
    [string]$Version = 'v0.13.0',
    [string]$RunnerArch = $env:RUNNER_ARCH,
    [string]$RunnerTemp = $env:RUNNER_TEMP
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Install-GitsignCore {
    [CmdletBinding()]
    param(
        [string]$Version = 'v0.13.0',
        [string]$RunnerArch,
        [string]$RunnerTemp
    )

    if ([string]::IsNullOrWhiteSpace($RunnerArch)) {
        throw 'RUNNER_ARCH is required.'
    }

    if ([string]::IsNullOrWhiteSpace($RunnerTemp)) {
        throw 'RUNNER_TEMP is required.'
    }

    $assetVersion = $Version.TrimStart('v')
    $assetConfig = switch ($RunnerArch) {
        'X64' {
            @{
                Asset  = "gitsign_${assetVersion}_linux_amd64"
                Sha256 = '011af11d57ad205b4ae4e3f41ecc8c64fbe0043c829190c0cb44fce6fce74bbb'
            }
        }
        'ARM64' {
            @{
                Asset  = "gitsign_${assetVersion}_linux_arm64"
                Sha256 = 'f0ec4732696b88f133ab6ef9ac2fe8be843ab9540bd1cc51dc4143142a858372'
            }
        }
        default {
            throw "Unsupported runner architecture: $RunnerArch"
        }
    }

    $asset = $assetConfig.Asset
    $assetUrl = "https://github.com/sigstore/gitsign/releases/download/$Version/$asset"
    $assetPath = Join-Path $RunnerTemp $asset
    Invoke-WebRequest -Uri $assetUrl -OutFile $assetPath

    $actualSha = (Get-FileHash -Path $assetPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha -ne $assetConfig.Sha256) {
        throw "Checksum verification failed for $asset. Expected $($assetConfig.Sha256) but got $actualSha"
    }

    $binDir = Join-Path $RunnerTemp 'bin'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $gitsign = Join-Path $binDir 'gitsign'
    Move-Item -Path $assetPath -Destination $gitsign -Force
    & chmod +x $gitsign

    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_PATH)) {
        Add-Content -Path $env:GITHUB_PATH -Value $binDir
    }

    & $gitsign version
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (Install-GitsignCore -Version $Version -RunnerArch $RunnerArch -RunnerTemp $RunnerTemp)
    }
    catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
