#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs the dependency pinning scan workflow step.
.DESCRIPTION
    Invokes Test-DependencyPinning.ps1, emits GitHub Actions outputs and annotations,
    writes the workflow summary, and enforces the configured compliance threshold.
.PARAMETER Threshold
    Compliance threshold percentage. Defaults to PINNING_THRESHOLD or 95.
.PARAMETER DependencyTypes
    Comma-separated dependency types to scan. Defaults to PINNING_DEPENDENCY_TYPES.
.PARAMETER SoftFail
    String boolean controlling threshold enforcement. Defaults to PINNING_SOFT_FAIL.
.PARAMETER UploadSarif
    String boolean controlling SARIF generation. Defaults to PINNING_UPLOAD_SARIF.
.PARAMETER ExcludePaths
    Comma-separated path globs to exclude. Defaults to PINNING_EXCLUDE_PATHS.
#>
[CmdletBinding()]
param(
    [string]$Threshold = $env:PINNING_THRESHOLD,
    [string]$DependencyTypes = $env:PINNING_DEPENDENCY_TYPES,
    [string]$SoftFail = $env:PINNING_SOFT_FAIL,
    [string]$UploadSarif = $env:PINNING_UPLOAD_SARIF,
    [string]$ExcludePaths = $env:PINNING_EXCLUDE_PATHS
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "../lib/Modules/CIHelpers.psm1") -Force

function Invoke-DependencyPinningWorkflowCore {
    [CmdletBinding()]
    param(
        [string]$Threshold = $env:PINNING_THRESHOLD,
        [string]$DependencyTypes = $env:PINNING_DEPENDENCY_TYPES,
        [string]$SoftFail = $env:PINNING_SOFT_FAIL,
        [string]$UploadSarif = $env:PINNING_UPLOAD_SARIF,
        [string]$ExcludePaths = $env:PINNING_EXCLUDE_PATHS,
        [scriptblock]$InvokeScanner = {
            param(
                [hashtable]$ScannerParams,
                [ref]$ExitCode
            )

            $global:LASTEXITCODE = 0
            & scripts/security/Test-DependencyPinning.ps1 @ScannerParams
            $ExitCode.Value = [int]$LASTEXITCODE
        }
    )

    Write-Host 'Validating dependency SHA pinning compliance...'

    $thresholdValue = if ([string]::IsNullOrWhiteSpace($Threshold)) { 95 } else { [int]$Threshold }
    if ($thresholdValue -lt 0 -or $thresholdValue -gt 100) {
        throw "Threshold must be between 0 and 100; got $thresholdValue"
    }

    $softFailEnabled = $SoftFail -eq 'true'
    $uploadSarifEnabled = $UploadSarif -eq 'true'

    New-Item -ItemType Directory -Force -Path logs | Out-Null

    $params = @{
        Path       = '.'
        Recursive  = $true
        Format     = 'json'
        OutputPath = 'logs/dependency-pinning-results.json'
        Threshold  = $thresholdValue
    }

    if (-not [string]::IsNullOrWhiteSpace($DependencyTypes)) {
        $params['IncludeTypes'] = $DependencyTypes
    }

    if (-not [string]::IsNullOrWhiteSpace($ExcludePaths)) {
        $params['ExcludePaths'] = $ExcludePaths
    }

    if (-not $softFailEnabled) {
        $params['FailOnUnpinned'] = $true
    }

    $scannerExitCode = 0
    & $InvokeScanner $params ([ref]$scannerExitCode)
    $jsonExitCode = $scannerExitCode

    if ($uploadSarifEnabled) {
        Write-Host 'Generating SARIF format for Security tab...'
        $sarifParams = $params.Clone()
        $sarifParams['Format'] = 'sarif'
        $sarifParams['OutputPath'] = 'logs/dependency-pinning-results.sarif'
        $null = $sarifParams.Remove('FailOnUnpinned')

        $scannerExitCode = 0
        & $InvokeScanner $sarifParams ([ref]$scannerExitCode)
        $sarifExitCode = $scannerExitCode
        if ($sarifExitCode -ne 0) {
            Write-CIAnnotation -Message 'Dependency pinning SARIF generation failed.' -Level Error
            return $sarifExitCode
        }
    }

    if (-not (Test-Path -Path logs/dependency-pinning-results.json)) {
        throw 'Failed to generate dependency pinning report'
    }

    $report = Get-Content -Path logs/dependency-pinning-results.json -Raw | ConvertFrom-Json
    $complianceScore = [double]$report.ComplianceScore
    $unpinnedCount = [int]$report.UnpinnedDependencies
    $isCompliant = $complianceScore -ge $thresholdValue
    $isCompliantText = $isCompliant.ToString().ToLowerInvariant()

    Set-CIOutput -Name 'compliance-score' -Value $complianceScore.ToString()
    Set-CIOutput -Name 'unpinned-count' -Value $unpinnedCount.ToString()
    Set-CIOutput -Name 'is-compliant' -Value $isCompliantText

    Write-Host "Compliance Score: $complianceScore%"
    Write-Host "Unpinned Dependencies: $unpinnedCount"
    Write-Host "Is Compliant (>=$thresholdValue%): $isCompliant"

    if ($unpinnedCount -gt 0) {
        foreach ($violation in $report.Violations) {
            $line = if ($null -ne $violation.Line) { [int]$violation.Line } else { 0 }
            $message = "Unpinned $($violation.Type) dependency: $($violation.Name)@$($violation.Version) (Severity: $($violation.Severity))"
            Write-CIAnnotation -Message $message -Level Warning -File $violation.File -Line $line
        }
    }

    $status = if ($isCompliant) { 'Compliant' } else { 'Non-Compliant' }
    $actionSection = if ($unpinnedCount -ne 0) {
        @"

### Action Required

**$unpinnedCount dependencies are not SHA-pinned.**

Review the warnings in the workflow log and pin dependencies to specific SHA commits.

"@
    }
    else {
        @"

### All Dependencies Pinned

All dependencies are properly SHA-pinned.

"@
    }

    $summary = @"
## Dependency Pinning Scan Results

| Metric | Value |
|--------|-------|
| Compliance Score | $complianceScore% |
| Unpinned Dependencies | $unpinnedCount |
| Status | $status |
$actionSection
"@
    Write-CIStepSummary -Content $summary

    if ($jsonExitCode -ne 0) {
        return $jsonExitCode
    }

    if (-not $softFailEnabled -and -not $isCompliant) {
        return 1
    }

    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (Invoke-DependencyPinningWorkflowCore `
                -Threshold $Threshold `
                -DependencyTypes $DependencyTypes `
                -SoftFail $SoftFail `
                -UploadSarif $UploadSarif `
                -ExcludePaths $ExcludePaths)
    }
    catch {
        Write-CIAnnotation -Message $_.Exception.Message -Level Error
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
