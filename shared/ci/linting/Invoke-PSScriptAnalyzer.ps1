#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs PSScriptAnalyzer on PowerShell files in the repository.

.DESCRIPTION
    Wrapper script for PSScriptAnalyzer that provides GitHub Actions integration,
    automatic module installation, and configurable file targeting.

.PARAMETER ChangedFilesOnly
    When specified, only analyze PowerShell files that have changed compared to the base branch.

.PARAMETER BaseBranch
    The base branch to compare against when using ChangedFilesOnly. Defaults to 'origin/main'.

.PARAMETER ConfigPath
    Path to the PSScriptAnalyzer settings file. Defaults to 'shared/ci/linting/PSScriptAnalyzer.psd1'.

.PARAMETER OutputPath
    Path for JSON results output. When specified, results are exported to this file.

.EXAMPLE
    ./Invoke-PSScriptAnalyzer.ps1
    Analyzes all PowerShell files in the repository.

.EXAMPLE
    ./Invoke-PSScriptAnalyzer.ps1 -ChangedFilesOnly -BaseBranch 'origin/main'
    Analyzes only changed PowerShell files compared to origin/main.
#>
[CmdletBinding()]
param(
    [switch]$ChangedFilesOnly,
    [string]$BaseBranch = 'origin/main',
    [string]$ConfigPath = '',
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../../../scripts/lib/Modules/CIHelpers.psm1") -Force

function Invoke-PSScriptAnalyzerCore {
    [CmdletBinding()]
    param(
        [switch]$ChangedFilesOnly,
        [string]$BaseBranch = 'origin/main',
        [string]$ConfigPath = '',
        [string]$OutputPath = ''
    )

    # Determine repository root
    $repoRoot = git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
    }

    # Set default config path if not specified
    if (-not $ConfigPath) {
        $ConfigPath = Join-Path $PSScriptRoot 'PSScriptAnalyzer.psd1'
    }

    # Ensure PSScriptAnalyzer module is installed
    if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
        Write-Host 'Installing PSScriptAnalyzer module...'
        Install-Module -Name PSScriptAnalyzer -Force -Scope CurrentUser -Repository PSGallery
    }
    Import-Module PSScriptAnalyzer -Force
    Write-Host "PSScriptAnalyzer version: $((Get-Module PSScriptAnalyzer).Version)"

    # Collect files to analyze
    $filesToAnalyze = @()

    if ($ChangedFilesOnly) {
        Write-Host "Analyzing changed files only (base: $BaseBranch)"
        $filesToAnalyze = @(Get-ChangedFilesFromGit -BaseBranch $BaseBranch -FileExtensions @('*.ps1', '*.psm1', '*.psd1'))
    } else {
        Write-Host 'Analyzing all PowerShell files in repository'
        $filesToAnalyze = @(Get-ChildItem -Path $repoRoot -Include '*.ps1', '*.psm1', '*.psd1' -Recurse -File |
            Where-Object { $_.FullName -notmatch '[\\/](\.git|node_modules|vendor)[\\/]' } |
            Select-Object -ExpandProperty FullName)
    }

    Write-Host "Found $(@($filesToAnalyze).Count) file(s) to analyze"
    if (@($filesToAnalyze).Count -gt 0) {
        $filesToAnalyze | ForEach-Object { Write-Host "  - $_" }
    }

    # Run analysis
    $allResults = @()
    $errorCount = 0
    $warningCount = 0

    if (@($filesToAnalyze).Count -gt 0) {
        foreach ($file in $filesToAnalyze) {
            $relativePath = $file
            if ($file.StartsWith($repoRoot)) {
                $relativePath = $file.Substring($repoRoot.Length).TrimStart('\', '/')
            }

            $analyzerParams = @{
                Path = $file
                Recurse = $false
            }

            if ((Test-Path $ConfigPath)) {
                $analyzerParams['Settings'] = $ConfigPath
            }

            $results = Invoke-ScriptAnalyzer @analyzerParams

            if ($results) {
                $allResults += $results

                foreach ($result in $results) {
                    $message = "$($result.RuleName): $($result.Message)"

                    $annotationLevel = switch ($result.Severity) {
                        'Error' { 'Error' }
                        'Warning' { 'Warning' }
                        'Information' { 'Notice' }
                        default { 'Notice' }
                    }

                    if ($result.Severity -eq 'Error') { $errorCount++ }
                    if ($result.Severity -eq 'Warning') { $warningCount++ }

                    Write-CIAnnotation -Message $message -Level $annotationLevel -File $relativePath -Line $result.Line -Column $result.Column
                }
            }
        }
    }

    # Export results if OutputPath specified
    if ($OutputPath) {
        $outputDir = Split-Path $OutputPath -Parent
        if ($outputDir -and -not (Test-Path $outputDir)) {
            New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
        }

        $exportData = @{
            timestamp = (Get-Date).ToString('o')
            totalFiles = @($filesToAnalyze).Count
            errorCount = $errorCount
            warningCount = $warningCount
            results = $allResults | ForEach-Object {
                @{
                    file = $_.ScriptPath
                    line = $_.Line
                    column = $_.Column
                    severity = $_.Severity.ToString()
                    rule = $_.RuleName
                    message = $_.Message
                }
            }
        }

        $exportData | ConvertTo-Json -Depth 10 | Set-Content -Path $OutputPath -Encoding UTF8
        Write-Host "Results exported to: $OutputPath"
    }

    # Summary
    Write-Host ''
    Write-Host "PSScriptAnalyzer Summary:"
    Write-Host "  Files analyzed: $(@($filesToAnalyze).Count)"
    Write-Host "  Errors: $errorCount"
    Write-Host "  Warnings: $warningCount"

    Set-CIOutput -Name "issues" -Value ($errorCount + $warningCount).ToString()
    Set-CIOutput -Name "errors" -Value $errorCount.ToString()
    Set-CIOutput -Name "warnings" -Value $warningCount.ToString()

    $summary = @"
## PSScriptAnalyzer Results

| Metric | Count |
|--------|-------|
| Files Analyzed | $(@($filesToAnalyze).Count) |
| Errors | $errorCount |
| Warnings | $warningCount |

"@
    Write-CIStepSummary -Content $summary

    if ($errorCount -gt 0 -or $warningCount -gt 0) {
        Set-CIEnv -Name "PSSCRIPTANALYZER_FAILED" -Value "true"
        if ($errorCount -gt 0) {
            Write-CIAnnotation -Message "PSScriptAnalyzer found $errorCount error(s) and $warningCount warning(s). Fix the issues above." -Level Error
        }
        else {
            Write-CIAnnotation -Message "PSScriptAnalyzer found $warningCount warning(s). Fix the issues above." -Level Warning
        }
        return 1
    }

    return 0
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-PSScriptAnalyzerCore -ChangedFilesOnly:$ChangedFilesOnly -BaseBranch $BaseBranch -ConfigPath $ConfigPath -OutputPath $OutputPath
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-PSScriptAnalyzer failed: $($_.Exception.Message)"
        Write-CIAnnotation -Message $_.Exception.Message -Level Error
        exit 1
    }
}
#endregion Main Execution
