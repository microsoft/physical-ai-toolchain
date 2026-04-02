#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs TFLint across Terraform directories.
.DESCRIPTION
    Wraps tflint --recursive with shared .tflint.hcl config. Reports violations
    via CI annotations and writes JSON results to logs/.
.PARAMETER ConfigPath
    Path to .tflint.hcl. Defaults to repo root .tflint.hcl.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/tflint-results.json.
.PARAMETER TerraformDir
    Directory containing Terraform files. Defaults to infrastructure/terraform.
#>

[CmdletBinding()]
param(
    [string]$ConfigPath,
    [string]$OutputPath,
    [string]$TerraformDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../../../shared/lib/Modules/CIHelpers.psm1") -Force

function Invoke-TFLintCore {
    [CmdletBinding()]
    param(
        [string]$ConfigPath,
        [string]$OutputPath,
        [string]$TerraformDir
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $ConfigPath) { $ConfigPath = Join-Path $repoRoot '.tflint.hcl' }
    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/tflint-results.json' }
    if (-not $TerraformDir) { $TerraformDir = Join-Path $repoRoot 'infrastructure/terraform' }

    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    if (-not (Get-Command tflint -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'tflint is not installed or not in PATH'
        return 1
    }

    $resolvedConfig = Resolve-Path $ConfigPath

    # Run TFLint with JSON output for parsing
    $jsonOutput = & tflint --recursive --chdir="$TerraformDir" --config $resolvedConfig --format json 2>&1
    $exitCode = $LASTEXITCODE

    $jsonOutput | Out-File -FilePath $OutputPath -Encoding utf8

    # Parse results for CI annotations
    $results = $null
    try {
        $results = $jsonOutput | ConvertFrom-Json -ErrorAction Stop
        if ($results.issues) {
            foreach ($issue in $results.issues) {
                $level = if ($issue.rule.severity -eq 'error') { 'Error' } else { 'Warning' }
                Write-CIAnnotation -Level $level -Message $issue.message `
                    -File $issue.range.filename -Line $issue.range.start.line
            }
        }
    }
    catch {
        Write-Warning "Failed to parse tflint JSON output: $($_.Exception.Message)"
    }

    $issueCount = if ($results.issues) { $results.issues.Count } else { 0 }
    $summary = "TFLint: $issueCount issue(s) found in $TerraformDir"
    Write-CIStepSummary -Content $summary
    Write-Host $summary

    return $exitCode
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-TFLintCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-TFLint failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
