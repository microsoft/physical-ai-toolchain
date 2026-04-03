#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Generates terraform-docs documentation for all Terraform modules and deployments.
.DESCRIPTION
    Discovers Terraform directories under infrastructure/terraform, runs terraform-docs
    in replace mode with the repo-root .terraform-docs.yml config to generate TERRAFORM.md
    files, and post-processes tables with markdown-table-formatter.
.PARAMETER Check
    Check mode: generate docs and verify no uncommitted changes (for CI).
.PARAMETER ConfigPreview
    Print configuration and exit without making changes.
.PARAMETER TerraformDir
    Root directory containing Terraform files. Defaults to infrastructure/terraform.
.PARAMETER ConfigPath
    Path to the .terraform-docs.yml configuration file. Defaults to repo root.
.PARAMETER PassthroughArgs
    Additional arguments. When --check is included, activates check mode.
#>

[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$ConfigPreview,
    [string]$TerraformDir,
    [string]$ConfigPath,
    [string[]]$PassthroughArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "lib/Modules/CIHelpers.psm1") -Force

function Update-TerraformDocsCore {
    [CmdletBinding()]
    param(
        [switch]$Check,
        [switch]$ConfigPreview,
        [string]$TerraformDir,
        [string]$ConfigPath,
        [string[]]$PassthroughArgs
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.FullName
    }

    if (-not $TerraformDir) { $TerraformDir = Join-Path $repoRoot 'infrastructure/terraform' }
    if (-not $ConfigPath) { $ConfigPath = Join-Path $repoRoot '.terraform-docs.yml' }

    $isCheckMode = $Check.IsPresent
    if ($PassthroughArgs -contains '--check') {
        $isCheckMode = $true
    }

    # Validate required tools
    foreach ($tool in @('terraform-docs', 'npx', 'git')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Write-CIAnnotation -Level Error -Message "$tool is not installed or not in PATH"
            return 1
        }
    }

    if (-not (Test-Path $ConfigPath)) {
        Write-CIAnnotation -Level Error -Message "Config file not found: $ConfigPath"
        return 1
    }

    # Discover Terraform directories (exclude tests/ and setup/)
    $tfDirs = Get-ChildItem -Path $TerraformDir -Filter '*.tf' -Recurse -File |
        Where-Object { $_.FullName -notmatch '[/\\]tests[/\\]' -and $_.FullName -notmatch '[/\\]setup[/\\]' } |
        ForEach-Object { $_.DirectoryName } |
        Sort-Object -Unique

    if ($ConfigPreview) {
        Write-Host '=== Configuration Preview ==='
        Write-Host "Base Directory : $TerraformDir"
        Write-Host "Config File    : $ConfigPath"
        Write-Host "Check Mode     : $isCheckMode"
        Write-Host "Directories    : $($tfDirs.Count)"
        foreach ($dir in $tfDirs) {
            $relDir = $dir.Substring($repoRoot.Length + 1)
            Write-Host "  Directory    : $relDir"
        }
        return 0
    }

    # Generate documentation
    Write-Host '=== Generating Terraform Documentation ==='

    $count = 0
    foreach ($dir in $tfDirs) {
        $relDir = $dir.Substring($repoRoot.Length + 1)
        Write-Host "Processing: $relDir"
        & terraform-docs markdown table --config $ConfigPath --output-file TERRAFORM.md $dir
        if ($LASTEXITCODE -ne 0) {
            Write-CIAnnotation -Level Error -Message "terraform-docs failed for $relDir"
            return 1
        }
        $count++
    }

    # Post-process tables
    Write-Host '=== Post-Processing Tables ==='

    foreach ($dir in $tfDirs) {
        $tfDoc = Join-Path $dir 'TERRAFORM.md'
        if (Test-Path $tfDoc) {
            & npx markdown-table-formatter $tfDoc
            if ($LASTEXITCODE -ne 0) {
                Write-CIAnnotation -Level Error -Message "markdown-table-formatter failed for $tfDoc"
                return 1
            }
        }
    }

    # Check mode: verify no uncommitted changes to generated files
    if ($isCheckMode) {
        Write-Host '=== Checking for Changes ==='
        $tfDocFiles = Get-ChildItem -Path $TerraformDir -Filter 'TERRAFORM.md' -Recurse -File |
            ForEach-Object { $_.FullName }
        $diffOutput = & git diff -- @tfDocFiles
        if ($diffOutput) {
            $diffOutput
            Write-Host 'Restoring modified files to original state...'
            & git checkout -- @tfDocFiles
            return 1
        }
    }

    $summary = "terraform-docs: $count directory(ies) processed"
    Write-CIStepSummary -Content $summary
    Write-Host $summary

    return 0
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Update-TerraformDocsCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Update-TerraformDocs failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
