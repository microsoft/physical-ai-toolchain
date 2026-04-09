#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs ShellCheck across shell scripts in the repository.
.DESCRIPTION
    Discovers .sh files recursively (excluding .venv/, external/, node_modules/, .git/),
    runs ShellCheck with JSON output, and writes results to logs/. Reports violations via
    CI annotations and generates a GitHub step summary.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/shellcheck-results.json.
.PARAMETER ChangedFilesOnly
    When set, only run lint if shell files have changed.
#>

[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$ChangedFilesOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../lib/Modules/CIHelpers.psm1") -Force

function Write-EmptyLintResults {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [string]$SummaryMessage
    )

    $results = @{
        timestamp         = (Get-Date -Format 'o')
        shellcheck_version = ''
        lint_passed        = $true
        error_count        = 0
        warning_count      = 0
        summary            = @{
            overall_passed = $true
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    $summaryContent = @(
        '### ShellCheck Results'
        ''
        $SummaryMessage
    ) -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent
}

function Invoke-ShellCheckCore {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [switch]$ChangedFilesOnly
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/shellcheck-results.json' }

    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    # Guard: ChangedFilesOnly
    if ($ChangedFilesOnly) {
        $changedFiles = @(Get-ChangedFilesFromGit -FileExtensions @('*.sh'))
        if ($changedFiles.Count -eq 0) {
            Write-Host 'No shell files changed — skipping lint'
            Write-EmptyLintResults -OutputPath $OutputPath -SummaryMessage 'No shell files changed — skipping lint.'
            return 0
        }
    }

    # Check shellcheck on PATH
    if (-not (Get-Command shellcheck -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'shellcheck not found on PATH. Install via apt-get or brew.'
        return 1
    }

    # Capture version (join array to string so -match populates $Matches under StrictMode)
    $scVersionOutput = (& shellcheck --version 2>$null) -join "`n"
    $scVersion = 'unknown'
    if ($scVersionOutput -match 'version:\s*([\d.]+)') {
        $scVersion = $Matches[1]
    }

    # Discover .sh files, excluding directories that should not be linted
    $excludeDirs = @('.venv', 'external', 'node_modules', '.git', 'docs/docusaurus')
    $allShFiles = @(Get-ChildItem -Path $repoRoot -Filter '*.sh' -Recurse -File | Where-Object {
        $relativePath = $_.FullName.Substring($repoRoot.Length + 1) -replace '\\', '/'
        $excluded = $false
        foreach ($dir in $excludeDirs) {
            if ($relativePath -like "$dir/*" -or $relativePath -like "*/$dir/*") {
                $excluded = $true
                break
            }
        }
        -not $excluded
    })

    if ($ChangedFilesOnly -and $changedFiles) {
        $changedSet = [System.Collections.Generic.HashSet[string]]::new(
            [StringComparer]::OrdinalIgnoreCase
        )
        foreach ($f in $changedFiles) {
            $fullPath = Join-Path $repoRoot $f
            [void]$changedSet.Add((Resolve-Path $fullPath -ErrorAction SilentlyContinue).Path)
        }
        $allShFiles = @($allShFiles | Where-Object { $changedSet.Contains($_.FullName) })
    }

    if ($allShFiles.Count -eq 0) {
        Write-Host 'No .sh files found to lint'
        Write-EmptyLintResults -OutputPath $OutputPath -SummaryMessage 'No `.sh` files found to lint.'
        return 0
    }

    Write-Host "Found $($allShFiles.Count) shell file(s) to lint"

    # Run shellcheck with JSON output on all files
    $errorCount = 0
    $warningCount = 0
    $allIssues = @()

    foreach ($file in $allShFiles) {
        $relativePath = $file.FullName.Substring($repoRoot.Length + 1).Replace('\', '/')
        $jsonOutput = & shellcheck --format=json $file.FullName 2>$null
        if ($jsonOutput) {
            $issues = $jsonOutput | ConvertFrom-Json
            foreach ($issue in $issues) {
                $level = switch ($issue.level) {
                    'error'   { 'Error' }
                    'warning' { 'Warning' }
                    default   { 'Notice' }
                }

                if ($level -eq 'Error') { $errorCount++ }
                elseif ($level -eq 'Warning') { $warningCount++ }

                Write-CIAnnotation -Level $level -Message "SC$($issue.code): $($issue.message)" `
                    -File $relativePath -Line $issue.line -Col $issue.column

                $allIssues += @{
                    file     = $relativePath
                    line     = $issue.line
                    column   = $issue.column
                    level    = $issue.level
                    code     = $issue.code
                    message  = $issue.message
                }
            }
        }
    }

    $lintPassed = ($errorCount -eq 0 -and $warningCount -eq 0)

    $results = @{
        timestamp          = (Get-Date -Format 'o')
        shellcheck_version = $scVersion
        lint_passed        = $lintPassed
        error_count        = $errorCount
        warning_count      = $warningCount
        files_checked      = $allShFiles.Count
        issues             = $allIssues
        summary            = @{
            overall_passed = $lintPassed
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    # Step summary
    $status = if ($lintPassed) { '✅ Passed' } else { "❌ Failed ($errorCount error(s), $warningCount warning(s))" }
    $summaryContent = @(
        '### ShellCheck Results'
        ''
        "**ShellCheck ($scVersion):** $status"
        ''
        "| Metric | Count |"
        "|--------|-------|"
        "| Files Checked | $($allShFiles.Count) |"
        "| Errors | $errorCount |"
        "| Warnings | $warningCount |"
    ) -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent

    if (-not $lintPassed) { return 1 } else { return 0 }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-ShellCheckCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-ShellCheck failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
