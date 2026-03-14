#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Invoke-PesterTests.ps1
#
# Purpose: Thin runner wrapping pester.config.ps1 for local and CI execution
# Author: Robotics-AI Team
#

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

<#
.SYNOPSIS
    Runs Pester tests for the repository.
.DESCRIPTION
    Wraps pester.config.ps1 to build a Pester 5.x configuration and invokes
    Invoke-Pester. Provides a convenient local entry point alongside the
    pester-tests.yml workflow which calls pester.config.ps1 directly.
.PARAMETER CI
    Enables CI mode: exit on failure, NUnit XML output, GitHub Actions annotations.
.PARAMETER CodeCoverage
    Enables JaCoCo code coverage collection.
.PARAMETER TestPath
    Paths to search for test files. Defaults to the scripts/tests directory.
.EXAMPLE
    ./Invoke-PesterTests.ps1
.EXAMPLE
    ./Invoke-PesterTests.ps1 -CI -CodeCoverage
.EXAMPLE
    ./Invoke-PesterTests.ps1 -TestPath ./security
#>
[CmdletBinding()]
param(
    [switch]$CI,
    [switch]$CodeCoverage,
    [string[]]$TestPath
)

$ErrorActionPreference = 'Stop'

$configParams = @{}
if ($CI.IsPresent) { $configParams['CI'] = $true }
if ($CodeCoverage.IsPresent) { $configParams['CodeCoverage'] = $true }
if ($TestPath) { $configParams['TestPath'] = $TestPath }

$config = & "$PSScriptRoot/pester.config.ps1" @configParams
$result = Invoke-Pester -Configuration $config

# Write step summary in CI environments when CIHelpers is available
if ($CI.IsPresent) {
    $ciHelpersPath = Join-Path $PSScriptRoot '../lib/Modules/CIHelpers.psm1'
    if (Test-Path $ciHelpersPath) {
        Import-Module $ciHelpersPath -Force
        if (Get-Command -Name Write-CIStepSummary -ErrorAction SilentlyContinue) {
            $summary = @"
## Pester Test Results

| Metric  | Count                    |
|---------|--------------------------|
| Run     | $($result.TotalCount)    |
| Passed  | $($result.PassedCount)   |
| Failed  | $($result.FailedCount)   |
| Skipped | $($result.SkippedCount)  |
"@
            Write-CIStepSummary -Content $summary
        }
    }
}
