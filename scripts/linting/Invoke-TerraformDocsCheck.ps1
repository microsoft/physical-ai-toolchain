#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Checks that terraform-docs generated documentation is up to date.
.DESCRIPTION
    Runs npm run docs:generate:tf -- --check to compare generated documentation against committed
    files. Reports drift via CI annotations and writes JSON results to logs/.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/terraform-docs-check-results.json.
.PARAMETER ChangedFilesOnly
    When set, only check if directories containing changed .tf files have doc drift.
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

function Invoke-TerraformDocsCheckCore {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [switch]$ChangedFilesOnly
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/terraform-docs-check-results.json' }

    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    if (-not (Get-Command terraform-docs -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'terraform-docs is not installed or not in PATH'
        return 1
    }

    $tdVersion = (& terraform-docs --version 2>&1 | Out-String).Trim()

    # Skip if no relevant files changed
    if ($ChangedFilesOnly) {
        $changedTf = @(Get-ChangedFilesFromGit -FileExtensions @('*.tf', '*.tfvars'))
        $changedConfig = @(Get-ChangedFilesFromGit -FileExtensions @('*.yml') | Where-Object { $_ -match '\.terraform-docs\.yml$' })

        if ($changedTf.Count -eq 0 -and $changedConfig.Count -eq 0) {
            Write-Host 'No Terraform or terraform-docs config files changed — skipping docs check'

            $results = @{
                timestamp              = (Get-Date -Format 'o')
                terraform_docs_version = $tdVersion
                skipped                = $true
                drift_detected         = $false
                drifted_files          = @()
                summary                = @{
                    files_drifted  = 0
                    overall_passed = $true
                }
            }

            $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
            Write-Host "Results written to $OutputPath"

            $summaryContent = "### Terraform Docs Check Results`n`n**Status:** ⏭️ Skipped (no relevant files changed)"
            Write-CIStepSummary -Content $summaryContent
            Write-Host $summaryContent
            return 0
        }
    }

    # Run terraform-docs check via npm script
    Write-Host 'Running npm run docs:generate:tf -- --check...'
    $output = & npm run docs:generate:tf -- --check 2>&1 | ForEach-Object { $_.ToString() }
    $exitCode = $LASTEXITCODE
    $driftDetected = ($exitCode -ne 0)
    $driftedFiles = @()

    if ($driftDetected) {
        # Parse output for drifted file paths from git diff output
        $driftedFiles = @($output | ForEach-Object {
                if ($_ -match 'diff --git a/(.+) b/') { $Matches[1] }
            } | Where-Object { $_ } | Sort-Object -Unique)

        foreach ($file in $driftedFiles) {
            Write-CIAnnotation -Level Error -Message "Documentation is out of date: $file. Run 'npm run docs:generate:tf' to regenerate." -File $file
        }

        if ($driftedFiles.Count -eq 0) {
            Write-CIAnnotation -Level Error -Message "terraform-docs detected documentation drift. Run 'npm run docs:generate:tf' to regenerate."
        }
    }

    # Build results
    $results = @{
        timestamp              = (Get-Date -Format 'o')
        terraform_docs_version = $tdVersion
        skipped                = $false
        drift_detected         = $driftDetected
        drifted_files          = $driftedFiles
        output                 = ($output -join "`n")
        summary                = @{
            files_drifted  = $driftedFiles.Count
            overall_passed = (-not $driftDetected)
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    # Step summary
    $summaryLines = @()
    $summaryLines += '### Terraform Docs Check Results'
    $summaryLines += ''

    if ($driftDetected) {
        $summaryLines += '**Status:** ❌ Documentation drift detected'
        $summaryLines += ''
        $summaryLines += 'Run `npm run docs:generate:tf` to regenerate documentation.'
        $summaryLines += ''
        if ($driftedFiles.Count -gt 0) {
            $summaryLines += '| File | Status |'
            $summaryLines += '|------|--------|'
            foreach ($file in $driftedFiles) {
                $summaryLines += "| ``$file`` | ❌ Out of date |"
            }
        }
    }
    else {
        $summaryLines += '**Status:** ✅ All documentation is up to date'
    }

    $summaryContent = $summaryLines -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent

    if ($driftDetected) { return 1 } else { return 0 }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-TerraformDocsCheckCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-TerraformDocsCheck failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
