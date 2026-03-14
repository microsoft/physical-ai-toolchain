#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Checks for URLs containing language-specific paths in repository files.

.DESCRIPTION
    Wrapper script for Link-Lang-Check.ps1 with CI platform integration,
    result logging, and configurable file targeting.

.PARAMETER Files
    Optional list of specific files to check. When omitted, all repository files are checked.

.EXAMPLE
    ./Invoke-LinkLanguageCheck.ps1
    Checks all files in the repository for language-path URLs.

.EXAMPLE
    ./Invoke-LinkLanguageCheck.ps1 -Files @('docs/README.md', 'CONTRIBUTING.md')
    Checks only the specified files.
#>
[CmdletBinding()]
param(
    [string[]]$Files
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../lib/Modules/CIHelpers.psm1") -Force

function Invoke-LinkLanguageCheckCore {
    [CmdletBinding()]
    param(
        [string[]]$Files
    )

    $repoRoot = git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Not in a git repository"
        return 1
    }

    $logsDir = Join-Path $repoRoot "logs"
    if (-not (Test-Path $logsDir)) {
        New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
    }

    Write-Host "Checking for URLs with language paths..." -ForegroundColor Cyan

    if ($Files -and $Files.Count -gt 0) {
        $rawOutput = & (Join-Path $PSScriptRoot "Link-Lang-Check.ps1") -Files $Files 2>&1
    }
    else {
        $rawOutput = & (Join-Path $PSScriptRoot "Link-Lang-Check.ps1") 2>&1
    }

    $jsonOutput = @($rawOutput | Where-Object { $_ -is [string] }) -join "`n"

    try {
        $results = $jsonOutput | ConvertFrom-Json

        if ($results -and @($results).Count -gt 0) {
            Write-Host "Found $(@($results).Count) URLs with 'en-us' language paths`n" -ForegroundColor Yellow

            foreach ($item in $results) {
                Write-CIAnnotation `
                    -Level Warning `
                    -Message "URL contains language path: $($item.original_url)" `
                    -File $item.file `
                    -Line $item.line_number
            }

            $outputData = @{
                timestamp = (Get-Date).ToUniversalTime().ToString("o")
                script    = "link-lang-check"
                summary   = @{
                    total_issues   = @($results).Count
                    files_affected = @($results | Select-Object -ExpandProperty file -Unique).Count
                }
                issues    = $results
            }
            $outputData | ConvertTo-Json -Depth 3 | Out-File (Join-Path $logsDir "link-lang-check-results.json") -Encoding utf8

            Set-CIOutput -Name "issues" -Value @($results).Count.ToString()
            Set-CIEnv -Name "LINK_LANG_FAILED" -Value "true"

            $uniqueFiles = $results | Select-Object -ExpandProperty file -Unique

            Write-CIStepSummary -Content @"
## Link Language Path Check Results

**Status**: Issues Found

Found $(@($results).Count) URL(s) containing language path 'en-us'.

**Why this matters:**
Language-specific URLs don't adapt to user preferences and may break for non-English users.

**To fix locally:**
``````powershell
scripts/linting/Link-Lang-Check.ps1 -Fix
``````

**Files affected:**
$(($uniqueFiles | ForEach-Object { $count = ($results | Where-Object file -eq $_).Count; "- $_ ($count occurrence(s))" }) -join "`n")
"@

            return 1
        }
        else {
            Write-Host "No URLs with language paths found" -ForegroundColor Green

            $emptyResults = @{
                timestamp = (Get-Date).ToUniversalTime().ToString("o")
                script    = "link-lang-check"
                summary   = @{
                    total_issues   = 0
                    files_affected = 0
                }
                issues    = @()
            }
            $emptyResults | ConvertTo-Json -Depth 3 | Out-File (Join-Path $logsDir "link-lang-check-results.json") -Encoding utf8

            Set-CIOutput -Name "issues" -Value "0"

            Write-CIStepSummary -Content @"
## Link Language Path Check Results

**Status**: Passed

No URLs with language-specific paths detected.
"@

            return 0
        }
    }
    catch {
        Write-Error "Error parsing results: $_"
        Write-Host "Raw output: $jsonOutput"
        return 1
    }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-LinkLanguageCheckCore -Files $Files
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-LinkLanguageCheck failed: $($_.Exception.Message)"
        Write-CIAnnotation -Message $_.Exception.Message -Level Error
        exit 1
    }
}
#endregion Main Execution
