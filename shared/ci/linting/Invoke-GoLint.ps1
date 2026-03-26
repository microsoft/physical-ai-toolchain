#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs golangci-lint across Go modules in the repository.
.DESCRIPTION
    Verifies golangci-lint is available (installing if needed via SHA256-verified binary),
    runs golangci-lint run, and writes JSON output to logs/. Reports violations via CI
    annotations and generates a GitHub step summary.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/go-lint-results.json.
.PARAMETER GoModuleDir
    Directory containing the Go module to lint. Defaults to infrastructure/terraform/e2e.
.PARAMETER ChangedFilesOnly
    When set, only run lint if Go-related files have changed.
#>

[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$GoModuleDir,
    [switch]$ChangedFilesOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../../../scripts/lib/Modules/CIHelpers.psm1") -Force

function Write-EmptyLintResults {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [string]$SummaryMessage
    )

    $results = @{
        timestamp             = (Get-Date -Format 'o')
        golangci_lint_version = ''
        lint_passed           = $true
        violation_count       = 0
        summary               = @{
            overall_passed = $true
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    $summaryContent = @(
        '### Go Lint Results'
        ''
        $SummaryMessage
    ) -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent
}

function Invoke-GoLintCore {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [string]$GoModuleDir,
        [switch]$ChangedFilesOnly
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/go-lint-results.json' }
    if (-not $GoModuleDir) { $GoModuleDir = Join-Path $repoRoot 'infrastructure/terraform/e2e' }

    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    # Guard: go.mod must exist
    $goModPath = Join-Path $GoModuleDir 'go.mod'
    if (-not (Test-Path $goModPath)) {
        Write-Host "No go.mod found in $GoModuleDir — skipping lint"
        Write-EmptyLintResults -OutputPath $OutputPath -SummaryMessage 'No `go.mod` found — nothing to lint.'
        return 0
    }

    # Guard: ChangedFilesOnly
    if ($ChangedFilesOnly) {
        $changedFiles = @(Get-ChangedFilesFromGit -FileExtensions @('*.go', 'go.mod', 'go.sum'))
        if ($changedFiles.Count -eq 0) {
            Write-Host 'No Go files changed — skipping lint'
            Write-EmptyLintResults -OutputPath $OutputPath -SummaryMessage 'No Go files changed — skipping lint.'
            return 0
        }
    }

    # Check golangci-lint on PATH; install if missing via SHA256-verified binary download
    if (-not (Get-Command golangci-lint -ErrorAction SilentlyContinue)) {
        Write-Host 'golangci-lint not found — installing via SHA256-verified binary...'
        $lintInstallVersion = '2.11.4'
        $lintExpectedSHA256 = '200c5b7503f67b59a6743ccf32133026c174e272b930ee79aa2aa6f37aca7ef1'
        $lintUrl = "https://github.com/golangci/golangci-lint/releases/download/v${lintInstallVersion}/golangci-lint-${lintInstallVersion}-linux-amd64.tar.gz"
        $lintTarball = '/tmp/golangci-lint.tar.gz'
        $goPathBin = (& go env GOPATH) + '/bin'

        & bash -c "set -euo pipefail && curl -fsSL -o '${lintTarball}' '${lintUrl}' && echo '${lintExpectedSHA256}  ${lintTarball}' | sha256sum -c --quiet - && mkdir -p '${goPathBin}' && tar -xzf '${lintTarball}' -C '${goPathBin}' --strip-components=1 'golangci-lint-${lintInstallVersion}-linux-amd64/golangci-lint' && rm -f '${lintTarball}'"
        if ($LASTEXITCODE -ne 0) {
            Write-CIAnnotation -Level Error -Message 'Failed to install golangci-lint'
            return 1
        }
        $env:PATH = $goPathBin + [IO.Path]::PathSeparator + $env:PATH
        if (-not (Get-Command golangci-lint -ErrorAction SilentlyContinue)) {
            Write-CIAnnotation -Level Error -Message 'golangci-lint not available after install attempt'
            return 1
        }
    }

    # Capture version
    $lintVersionOutput = & golangci-lint version 2>$null
    $lintVersion = if ($lintVersionOutput -match 'v([\d.]+)') { $Matches[0] } else { 'unknown' }

    # Run golangci-lint
    $lintPassed = $true
    Push-Location $GoModuleDir
    try {
        $lintOutput = & golangci-lint run './...' 2>&1
        if ($LASTEXITCODE -ne 0) {
            $lintPassed = $false
            Write-CIAnnotation -Level Error -Message "golangci-lint failed in $GoModuleDir"
        }

        $violationCount = 0
        if (-not $lintPassed -and $lintOutput) {
            $outputLines = @($lintOutput | ForEach-Object { $_.ToString() } | Where-Object { $_ -match '^\S+:\d+' })
            $violationCount = $outputLines.Count
        }

        $results = @{
            timestamp             = (Get-Date -Format 'o')
            golangci_lint_version = $lintVersion
            lint_passed           = $lintPassed
            violation_count       = $violationCount
            summary               = @{
                overall_passed = $lintPassed
            }
        }

        $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
        Write-Host "Results written to $OutputPath"

        # Step summary
        $status = if ($lintPassed) { '✅ Passed' } else { "❌ Failed ($violationCount violation(s))" }
        $summaryContent = @(
            '### Go Lint Results'
            ''
            "**golangci-lint:** $status"
        ) -join "`n"
        Write-CIStepSummary -Content $summaryContent
        Write-Host $summaryContent
    }
    finally {
        Pop-Location
    }

    if ($lintPassed) { return 0 } else { return 1 }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-GoLintCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-GoLint failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
