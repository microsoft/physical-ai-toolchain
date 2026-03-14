#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs actionlint on GitHub Actions workflow files in the repository.
.DESCRIPTION
    Wrapper script for actionlint that provides GitHub Actions integration,
    changed-files-only mode, and structured JSON output.
.PARAMETER ChangedFilesOnly
    When specified, only lint workflow files that have changed compared to the base branch.
.PARAMETER BaseBranch
    The base branch to compare against when using ChangedFilesOnly. Defaults to 'origin/main'.
.PARAMETER OutputPath
    Path for JSON results output. Defaults to 'logs/yaml-lint-results.json'.
.EXAMPLE
    ./Invoke-YamlLint.ps1
    Lints all GitHub Actions workflow files in the repository.
.EXAMPLE
    ./Invoke-YamlLint.ps1 -ChangedFilesOnly
    Lints only changed workflow files compared to origin/main.
#>
[CmdletBinding()]
param(
    [switch]$ChangedFilesOnly,
    [string]$BaseBranch = 'origin/main',
    [string]$OutputPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../lib/Modules/CIHelpers.psm1") -Force

function Invoke-YamlLintCore {
    [CmdletBinding()]
    param(
        [switch]$ChangedFilesOnly,
        [string]$BaseBranch = 'origin/main',
        [string]$OutputPath = ''
    )

    # Determine repository root
    $repoRoot = git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.FullName
    }

    # Set default output path
    if (-not $OutputPath) {
        $OutputPath = Join-Path $repoRoot 'logs/yaml-lint-results.json'
    }

    # Verify actionlint is available
    if (-not (Get-Command 'actionlint' -ErrorAction SilentlyContinue)) {
        Write-Error "actionlint is not installed or not on PATH. Install via: https://github.com/rhysd/actionlint"
        return 1
    }

    # Collect workflow files
    $filesToLint = @()

    if ($ChangedFilesOnly) {
        Write-Host "Linting changed workflow files only (base: $BaseBranch)"
        $allChanged = @(Get-ChangedFilesFromGit -BaseBranch $BaseBranch -FileExtensions @('*.yml', '*.yaml'))
        $filesToLint = @($allChanged | Where-Object { $_ -match '\.github[\\/]workflows[\\/]' })
    } else {
        Write-Host 'Linting all GitHub Actions workflow files'
        $workflowDir = Join-Path $repoRoot '.github/workflows'
        if (Test-Path $workflowDir) {
            $filesToLint = @(Get-ChildItem -Path $workflowDir -Recurse -Include '*.yml', '*.yaml' -File |
                Select-Object -ExpandProperty FullName)
        }
    }

    Write-Host "Found $(@($filesToLint).Count) workflow file(s) to lint"
    if (@($filesToLint).Count -gt 0) {
        $filesToLint | ForEach-Object { Write-Host "  - $_" }
    }

    if (@($filesToLint).Count -eq 0) {
        Write-Host 'No workflow files to lint'
        Set-CIOutput -Name "issues" -Value "0"
        $summary = @"
## YAML Lint Results

No workflow files to lint.
"@
        Write-CIStepSummary -Content $summary
        return 0
    }

    # Run actionlint with JSON output
    $jsonOutput = $null
    try {
        $jsonOutput = & actionlint -format '{{json .}}' @filesToLint 2>&1
    } catch {
        Write-Verbose "actionlint returned non-zero exit code: $_"
    }

    # Parse results
    $issues = @()
    if ($jsonOutput) {
        $jsonString = ($jsonOutput | Out-String).Trim()
        if ($jsonString) {
            try {
                $parsed = $jsonString | ConvertFrom-Json
                $issues = @($parsed)
            } catch {
                Write-Warning "Failed to parse actionlint JSON output: $($_.Exception.Message)"
            }
        }
    }

    Write-Host "actionlint found $(@($issues).Count) issue(s)"

    # Process issues and write CI annotations
    $errorCount = 0
    $warningCount = 0

    foreach ($issue in $issues) {
        $filePath = $issue.filepath
        if ($filePath -and $repoRoot -and $filePath.StartsWith($repoRoot)) {
            $filePath = $filePath.Substring($repoRoot.Length).TrimStart('\', '/')
        }

        $level = if ($issue.kind -eq 'warning') { 'Warning' } else { 'Error' }
        if ($level -eq 'Error') { $errorCount++ } else { $warningCount++ }

        $message = $issue.message
        Write-CIAnnotation -Message $message -Level $level -File $filePath -Line $issue.line -Column $issue.column
    }

    # Export results
    $outputDir = Split-Path $OutputPath -Parent
    if ($outputDir -and -not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    }

    $exportData = @{
        timestamp = (Get-Date).ToString('o')
        totalFiles = @($filesToLint).Count
        errorCount = $errorCount
        warningCount = $warningCount
        issues = $issues | ForEach-Object {
            @{
                file = $_.filepath
                line = $_.line
                column = $_.column
                kind = $_.kind
                message = $_.message
            }
        }
    }

    $exportData | ConvertTo-Json -Depth 10 | Set-Content -Path $OutputPath -Encoding UTF8
    Write-Host "Results exported to: $OutputPath"

    # CI outputs and summary
    Set-CIOutput -Name "issues" -Value (@($issues).Count).ToString()
    Set-CIOutput -Name "errors" -Value $errorCount.ToString()
    Set-CIOutput -Name "warnings" -Value $warningCount.ToString()

    $summary = @"
## YAML Lint Results

| Metric | Count |
|--------|-------|
| Workflow Files | $(@($filesToLint).Count) |
| Errors | $errorCount |
| Warnings | $warningCount |

"@
    Write-CIStepSummary -Content $summary

    if ($errorCount -gt 0) {
        Set-CIEnv -Name "YAML_LINT_FAILED" -Value "true"
        Write-CIAnnotation -Message "actionlint found $errorCount error(s). Fix the issues above." -Level Error
        return 1
    }

    return 0
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-YamlLintCore -ChangedFilesOnly:$ChangedFilesOnly -BaseBranch $BaseBranch -OutputPath $OutputPath
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-YamlLint failed: $($_.Exception.Message)"
        Write-CIAnnotation -Message $_.Exception.Message -Level Error
        exit 1
    }
}
#endregion Main Execution
