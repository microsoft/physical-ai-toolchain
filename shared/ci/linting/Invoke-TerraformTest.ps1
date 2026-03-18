#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs terraform test across Terraform modules that contain tests/ directories.
.DESCRIPTION
    Discovers modules with tests/ subdirectories, runs terraform init -backend=false
    and terraform test -json for each, parses results, and writes JSON output to logs/.
    Reports failures via CI annotations and generates a GitHub step summary.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/terraform-test-results.json.
.PARAMETER TerraformDir
    Root directory containing Terraform files. Defaults to infrastructure/terraform.
.PARAMETER ChangedFilesOnly
    When set, only test modules containing changed .tf/.tftest.hcl files.
#>

[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$TerraformDir,
    [switch]$ChangedFilesOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../../../scripts/lib/Modules/CIHelpers.psm1") -Force

function Invoke-TerraformTestCore {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [string]$TerraformDir,
        [switch]$ChangedFilesOnly
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/terraform-test-results.json' }
    if (-not $TerraformDir) { $TerraformDir = Join-Path $repoRoot 'infrastructure/terraform' }

    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'terraform is not installed or not in PATH'
        return 1
    }

    $terraformVersion = & terraform version -json 2>$null | ConvertFrom-Json
    $versionString = if ($terraformVersion) { $terraformVersion.terraform_version } else { 'unknown' }

    # Discover modules with tests/ subdirectories
    $testModuleDirs = @()
    $modulesPath = Join-Path $TerraformDir 'modules'
    if (Test-Path $modulesPath) {
        $testModuleDirs += @(Get-ChildItem -Path $modulesPath -Recurse -Directory -Filter 'tests' |
            Where-Object { $_.Parent.FullName -ne $modulesPath } |
            ForEach-Object { $_.Parent.FullName })
    }

    # Standalone deployment directories with tests/
    $standaloneDirs = @(Get-ChildItem -Path $TerraformDir -Directory |
        Where-Object { $_.Name -ne 'modules' -and (Test-Path (Join-Path $_.FullName 'tests')) } |
        ForEach-Object { $_.FullName })
    $testModuleDirs += $standaloneDirs

    # Filter by changed files when requested
    if ($ChangedFilesOnly -and $testModuleDirs.Count -gt 0) {
        $changedFiles = @(Get-ChangedFilesFromGit -FileExtensions @('*.tf', '*.tftest.hcl'))
        if ($changedFiles.Count -eq 0) {
            Write-Host 'No Terraform test files changed — skipping tests'
            $testModuleDirs = @()
        }
        else {
            $testModuleDirs = @($testModuleDirs | Where-Object {
                    $moduleRelPath = $_ -replace [regex]::Escape($repoRoot + [IO.Path]::DirectorySeparatorChar), '' `
                        -replace [regex]::Escape($repoRoot + '/'), ''
                    $hasChanged = $false
                    foreach ($cf in $changedFiles) {
                        $normalizedCf = $cf -replace '\\', '/'
                        $normalizedMod = $moduleRelPath -replace '\\', '/'
                        if ($normalizedCf.StartsWith("$normalizedMod/")) {
                            $hasChanged = $true
                            break
                        }
                    }
                    $hasChanged
                })
        }
    }

    # Handle zero test directories
    if ($testModuleDirs.Count -eq 0) {
        Write-Host '0 modules with tests found — nothing to test'

        $results = @{
            timestamp         = (Get-Date -Format 'o')
            terraform_version = $versionString
            modules           = @()
            summary           = @{
                modules_tested  = 0
                modules_passed  = 0
                modules_skipped = 0
                total_passed    = 0
                total_failed    = 0
                total_errors    = 0
                overall_passed  = $true
            }
        }

        $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
        Write-Host "Results written to $OutputPath"

        $summaryContent = @(
            '### Terraform Test Results'
            ''
            '0 modules tested — no `tests/` directories found.'
        ) -join "`n"
        Write-CIStepSummary -Content $summaryContent
        Write-Host $summaryContent
        return 0
    }

    # Run tests per module
    $moduleResults = @()
    $totalPassed = 0
    $totalFailed = 0
    $totalErrors = 0

    foreach ($moduleDir in $testModuleDirs) {
        $displayPath = $moduleDir -replace [regex]::Escape($repoRoot + [IO.Path]::DirectorySeparatorChar), '' `
            -replace [regex]::Escape($repoRoot + '/'), ''

        Write-Host "Testing module: $displayPath"

        Push-Location $moduleDir
        try {
            $initOutput = & terraform init -backend=false -input=false -no-color 2>&1
            if ($LASTEXITCODE -ne 0) {
                $totalErrors++
                $moduleResults += @{
                    path    = $displayPath
                    passed  = 0
                    failed  = 0
                    errors  = 1
                    skipped = $false
                }
                Write-CIAnnotation -Level Error -Message "terraform init failed in $displayPath"
                continue
            }

            $testOutput = & terraform test -json -no-color 2>&1
            $testExitCode = $LASTEXITCODE

            $modulePassed = 0
            $moduleFailed = 0
            $moduleErrors = 0

            foreach ($line in $testOutput) {
                $lineStr = $line.ToString().Trim()
                if ([string]::IsNullOrWhiteSpace($lineStr)) { continue }

                try {
                    $jsonObj = $lineStr | ConvertFrom-Json -ErrorAction Stop
                }
                catch {
                    continue
                }

                if ($jsonObj.type -eq 'test_run' -and $jsonObj.test_run.progress -eq 'complete') {
                    $status = $jsonObj.test_run.status
                    if ($status -eq 'pass') {
                        $modulePassed++
                    }
                    elseif ($status -eq 'fail') {
                        $moduleFailed++
                        $testName = "$($jsonObj.test_run.run)"
                        Write-CIAnnotation -Level Error -Message "Test failed in ${displayPath}: $testName"
                    }
                    elseif ($status -eq 'error') {
                        $moduleErrors++
                        $testName = "$($jsonObj.test_run.run)"
                        Write-CIAnnotation -Level Error -Message "Test error in ${displayPath}: $testName"
                    }
                }
            }

            $totalPassed += $modulePassed
            $totalFailed += $moduleFailed
            $totalErrors += $moduleErrors

            $moduleResults += @{
                path    = $displayPath
                passed  = $modulePassed
                failed  = $moduleFailed
                errors  = $moduleErrors
                skipped = $false
            }
        }
        finally {
            Pop-Location
        }
    }

    # Build results object
    $modulesPassed = @($moduleResults | Where-Object { $_.failed -eq 0 -and $_.errors -eq 0 }).Count
    $overallPassed = ($totalFailed -eq 0 -and $totalErrors -eq 0)

    $results = @{
        timestamp         = (Get-Date -Format 'o')
        terraform_version = $versionString
        modules           = @($moduleResults | ForEach-Object {
                @{
                    path    = $_.path
                    passed  = $_.passed
                    failed  = $_.failed
                    errors  = $_.errors
                    skipped = if ($_.skipped) { $true } else { $false }
                }
            })
        summary           = @{
            modules_tested  = $moduleResults.Count
            modules_passed  = $modulesPassed
            modules_skipped = 0
            total_passed    = $totalPassed
            total_failed    = $totalFailed
            total_errors    = $totalErrors
            overall_passed  = $overallPassed
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    # Step summary
    $summaryLines = @()
    $summaryLines += '### Terraform Test Results'
    $summaryLines += ''
    $summaryLines += '| Module | Passed | Failed | Errors | Status |'
    $summaryLines += '|--------|--------|--------|--------|--------|'

    foreach ($mr in $moduleResults) {
        $status = if ($mr.failed -eq 0 -and $mr.errors -eq 0) {
            '✅ Passed'
        }
        else {
            '❌ Failed'
        }
        $summaryLines += "| $($mr.path) | $($mr.passed) | $($mr.failed) | $($mr.errors) | $status |"
    }

    $summaryLines += ''
    $summaryLines += "**Total:** $totalPassed passed, $totalFailed failed, $totalErrors errors"

    $summaryContent = $summaryLines -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent

    if ($overallPassed) { return 0 } else { return 1 }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-TerraformTestCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-TerraformTest failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
