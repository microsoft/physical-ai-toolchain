#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs terraform fmt and terraform validate across Terraform deployment directories.
.DESCRIPTION
    Checks formatting with terraform fmt -check -recursive and validates each deployment
    directory with terraform init -backend=false + terraform validate. Reports violations
    via CI annotations and writes JSON results to logs/.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/terraform-validation-results.json.
.PARAMETER TerraformDir
    Root directory containing Terraform files. Defaults to infrastructure/terraform.
.PARAMETER ChangedFilesOnly
    When set, only validate directories containing changed .tf/.tfvars files.
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

function Invoke-TerraformValidationCore {
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

    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/terraform-validation-results.json' }
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

    $deployDirs = @('.', 'vpn', 'dns', 'automation')

    # Determine which directories to validate
    $dirsToValidate = if ($ChangedFilesOnly) {
        $changedFiles = Get-ChangedFilesFromGit -FileExtensions @('*.tf', '*.tfvars')
        if ($changedFiles.Count -eq 0) {
            Write-Host 'No Terraform files changed — skipping validation'
            @()
        }
        else {
            $changedFiles | ForEach-Object {
                $relPath = $_ -replace '^infrastructure/terraform/', ''
                if ($relPath -match '^(vpn|dns|automation)/') {
                    $Matches[1]
                }
                else {
                    '.'
                }
            } | Sort-Object -Unique
        }
    }
    else {
        $deployDirs
    }

    # Format check — always runs regardless of ChangedFilesOnly
    $fmtOutput = & terraform fmt -check -recursive -diff $TerraformDir 2>&1
    $fmtExitCode = $LASTEXITCODE
    $fmtPassed = ($fmtExitCode -eq 0)

    $unformattedFiles = @()
    if (-not $fmtPassed) {
        $unformattedFiles = @($fmtOutput | ForEach-Object { $_.ToString() } | Where-Object { $_ -match '\.tf$' })
        foreach ($file in $unformattedFiles) {
            Write-CIAnnotation -Level Warning -Message "File is not formatted: $file" -File $file
        }
    }

    # Validation loop
    $validationResults = @()
    foreach ($dir in $deployDirs) {
        $fullPath = if ($dir -eq '.') { $TerraformDir } else { Join-Path $TerraformDir $dir }
        $displayPath = if ($dir -eq '.') { $TerraformDir } else { "$TerraformDir/$dir" }

        if ($dir -notin $dirsToValidate) {
            $validationResults += @{
                directory = $displayPath
                passed    = $true
                skipped   = $true
                errors    = @()
                warnings  = @()
            }
            continue
        }

        Push-Location $fullPath
        try {
            & terraform init -backend=false -input=false -no-color 2>&1 | Out-Null
            $validateOutput = & terraform validate -json -no-color 2>&1
            $validateExit = $LASTEXITCODE

            $validateResult = $validateOutput | Out-String | ConvertFrom-Json

            $errors = @()
            $warnings = @()

            if ($validateResult.diagnostics) {
                foreach ($diag in $validateResult.diagnostics) {
                    $diagFile = if ($diag.range.filename) { "$displayPath/$($diag.range.filename)" } else { $null }
                    $diagLine = if ($diag.range.start.line) { $diag.range.start.line } else { 0 }

                    $entry = @{
                        severity = $diag.severity
                        summary  = $diag.summary
                        detail   = $diag.detail
                        file     = $diagFile
                        line     = $diagLine
                    }

                    if ($diag.severity -eq 'error') {
                        $errors += $entry
                        $annotParams = @{ Level = 'Error'; Message = $diag.summary }
                        if ($diagFile) { $annotParams.File = $diagFile }
                        if ($diagLine -gt 0) { $annotParams.Line = $diagLine }
                        Write-CIAnnotation @annotParams
                    }
                    else {
                        $warnings += $entry
                        $annotParams = @{ Level = 'Warning'; Message = $diag.summary }
                        if ($diagFile) { $annotParams.File = $diagFile }
                        if ($diagLine -gt 0) { $annotParams.Line = $diagLine }
                        Write-CIAnnotation @annotParams
                    }
                }
            }

            $validationResults += @{
                directory = $displayPath
                passed    = ($validateExit -eq 0)
                skipped   = $false
                errors    = $errors
                warnings  = $warnings
            }
        }
        finally {
            Pop-Location
        }
    }

    # Build results object
    $directoriesChecked = ($validationResults | Where-Object { -not $_.skipped }).Count
    $directoriesPassed = ($validationResults | Where-Object { -not $_.skipped -and $_.passed }).Count
    $directoriesSkipped = ($validationResults | Where-Object { $_.skipped }).Count
    $overallPassed = $fmtPassed -and ($directoriesChecked -eq $directoriesPassed)

    $results = @{
        timestamp         = (Get-Date -Format 'o')
        terraform_version = $versionString
        format_check      = @{
            passed           = $fmtPassed
            unformatted_files = $unformattedFiles
        }
        validation        = @($validationResults | ForEach-Object {
            @{
                directory = $_.directory
                passed    = $_.passed
                skipped   = if ($_.skipped) { $true } else { $false }
                errors    = $_.errors
                warnings  = $_.warnings
            }
        })
        summary           = @{
            directories_checked = $directoriesChecked
            directories_passed  = $directoriesPassed
            directories_skipped = $directoriesSkipped
            format_passed       = $fmtPassed
            overall_passed      = $overallPassed
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    # Step summary
    $summaryLines = @()
    $summaryLines += '### Terraform Validation Results'
    $summaryLines += ''
    $summaryLines += '| Check | Status |'
    $summaryLines += '|-------|--------|'

    $fmtStatus = if ($fmtPassed) { '✅ Passed' } else { '❌ Failed' }
    $summaryLines += "| Format | $fmtStatus |"

    foreach ($vr in $validationResults) {
        $status = if ($vr.skipped) {
            '⏭️ Skipped'
        }
        elseif ($vr.passed) {
            '✅ Passed'
        }
        else {
            '❌ Failed'
        }
        $summaryLines += "| $($vr.directory) | $status |"
    }

    $summaryContent = $summaryLines -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent

    if ($overallPassed) { return 0 } else { return 1 }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-TerraformValidationCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-TerraformValidation failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
