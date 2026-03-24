#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Runs Go tests and golangci-lint for Go modules in the repository.
.DESCRIPTION
    Verifies Go and golangci-lint are available, runs golangci-lint run and go test -json,
    parses results, and writes JSON output to logs/. Reports failures via CI annotations
    and generates a GitHub step summary.
.PARAMETER OutputPath
    Path for JSON results. Defaults to logs/go-test-results.json.
.PARAMETER CoverageOutput
    Path for coverage profile. Defaults to logs/go-coverage.out.
.PARAMETER GoTestDir
    Directory containing the Go module to test. Defaults to infrastructure/terraform/e2e.
.PARAMETER ChangedFilesOnly
    When set, only run tests if Go-related files have changed.
#>

[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$CoverageOutput,
    [string]$GoTestDir,
    [switch]$ChangedFilesOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "Modules/LintingHelpers.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "../../../scripts/lib/Modules/CIHelpers.psm1") -Force

function Write-EmptyResults {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [string]$SummaryMessage
    )

    $results = @{
        timestamp             = (Get-Date -Format 'o')
        go_version            = ''
        golangci_lint_version = ''
        lint_passed           = $true
        packages              = @()
        summary               = @{
            packages_tested  = 0
            packages_passed  = 0
            packages_skipped = 0
            total_passed     = 0
            total_failed     = 0
            total_skipped    = 0
            total_errors     = 0
            overall_passed   = $true
        }
    }

    $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
    Write-Host "Results written to $OutputPath"

    $summaryContent = @(
        '### Go Test Results'
        ''
        $SummaryMessage
    ) -join "`n"
    Write-CIStepSummary -Content $summaryContent
    Write-Host $summaryContent
}

function Invoke-GoTestCore {
    [CmdletBinding()]
    param(
        [string]$OutputPath,
        [string]$CoverageOutput,
        [string]$GoTestDir,
        [switch]$ChangedFilesOnly
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/go-test-results.json' }
    if (-not $CoverageOutput) { $CoverageOutput = Join-Path $repoRoot 'logs/go-coverage.out' }
    if (-not $GoTestDir) { $GoTestDir = Join-Path $repoRoot 'infrastructure/terraform/e2e' }

    $outputDir = Split-Path $OutputPath -Parent
    if (-not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    # Guard: go.mod must exist
    $goModPath = Join-Path $GoTestDir 'go.mod'
    if (-not (Test-Path $goModPath)) {
        Write-Host "No go.mod found in $GoTestDir — skipping tests"
        Write-EmptyResults -OutputPath $OutputPath -SummaryMessage 'No `go.mod` found — nothing to test.'
        return 0
    }

    # Guard: ChangedFilesOnly
    if ($ChangedFilesOnly) {
        $changedFiles = @(Get-ChangedFilesFromGit -FileExtensions @('*.go', 'go.mod', 'go.sum'))
        if ($changedFiles.Count -eq 0) {
            Write-Host 'No Go files changed — skipping tests'
            Write-EmptyResults -OutputPath $OutputPath -SummaryMessage 'No Go files changed — skipping tests.'
            return 0
        }
    }

    # Verify go on PATH
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'go is not installed or not in PATH'
        return 1
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

    # Capture versions
    $goVersionOutput = & go version 2>$null
    $goVersion = if ($goVersionOutput -match 'go([\d.]+)') { $Matches[0] } else { 'unknown' }

    $lintVersionOutput = & golangci-lint version 2>$null
    $lintVersion = if ($lintVersionOutput -match 'v([\d.]+)') { $Matches[0] } else { 'unknown' }

    # Run golangci-lint
    $lintPassed = $true
    Push-Location $GoTestDir
    try {
        $null = & golangci-lint run './...' 2>&1
        if ($LASTEXITCODE -ne 0) {
            $lintPassed = $false
            Write-CIAnnotation -Level Error -Message "golangci-lint failed in $GoTestDir"
        }

        # Run go test
        $testOutput = & go test -race -coverprofile=$CoverageOutput -covermode=atomic -v -json './...' 2>&1

        # Parse JSON output line by line
        $packageMap = @{}
        foreach ($line in $testOutput) {
            $lineStr = $line.ToString().Trim()
            if ([string]::IsNullOrWhiteSpace($lineStr)) { continue }

            try {
                $testEvent = $lineStr | ConvertFrom-Json -ErrorAction Stop
            }
            catch {
                continue
            }

            $pkg = $testEvent.Package
            if (-not $pkg) { continue }

            if (-not $packageMap.ContainsKey($pkg)) {
                $packageMap[$pkg] = @{
                    path      = $pkg
                    passed    = 0
                    failed    = 0
                    skipped   = 0
                    elapsed   = 0.0
                    test_runs = [System.Collections.ArrayList]@()
                }
            }

            $action = $testEvent.Action

            if ($testEvent.PSObject.Properties['Test'] -and $testEvent.Test) {
                # Per-test event
                if ($action -eq 'pass') {
                    $packageMap[$pkg].passed++
                    $elapsed = if ($testEvent.PSObject.Properties['Elapsed']) { $testEvent.Elapsed } else { 0.0 }
                    $null = $packageMap[$pkg].test_runs.Add(@{
                            name    = $testEvent.Test
                            status  = 'pass'
                            elapsed = $elapsed
                        })
                }
                elseif ($action -eq 'fail') {
                    $packageMap[$pkg].failed++
                    $elapsed = if ($testEvent.PSObject.Properties['Elapsed']) { $testEvent.Elapsed } else { 0.0 }
                    $null = $packageMap[$pkg].test_runs.Add(@{
                            name    = $testEvent.Test
                            status  = 'fail'
                            elapsed = $elapsed
                        })
                }
                elseif ($action -eq 'skip') {
                    $packageMap[$pkg].skipped++
                    $null = $packageMap[$pkg].test_runs.Add(@{
                            name    = $testEvent.Test
                            status  = 'skip'
                            elapsed = 0.0
                        })
                }
            }
            elseif ($action -eq 'pass' -or $action -eq 'fail') {
                # Package-level summary event
                if ($testEvent.PSObject.Properties['Elapsed']) {
                    $packageMap[$pkg].elapsed = $testEvent.Elapsed
                }
            }
        }

        # Build results
        $packages = @($packageMap.Values)
        $totalPassed = 0
        $totalFailed = 0
        $totalSkipped = 0
        foreach ($pkg in $packages) {
            $totalPassed += $pkg.passed
            $totalFailed += $pkg.failed
            $totalSkipped += $pkg.skipped
        }

        $packagesPassed = @($packages | Where-Object { $_.failed -eq 0 }).Count
        $packagesSkipped = @($packages | Where-Object { $_.passed -eq 0 -and $_.failed -eq 0 -and $_.skipped -gt 0 }).Count
        $overallPassed = $lintPassed -and ($totalFailed -eq 0)

        $results = @{
            timestamp             = (Get-Date -Format 'o')
            go_version            = $goVersion
            golangci_lint_version = $lintVersion
            lint_passed           = $lintPassed
            packages              = @($packages | ForEach-Object {
                    @{
                        path      = $_.path
                        passed    = $_.passed
                        failed    = $_.failed
                        skipped   = $_.skipped
                        elapsed   = $_.elapsed
                        test_runs = @($_.test_runs)
                    }
                })
            summary               = @{
                packages_tested  = $packages.Count
                packages_passed  = $packagesPassed
                packages_skipped = $packagesSkipped
                total_passed     = $totalPassed
                total_failed     = $totalFailed
                total_skipped    = $totalSkipped
                total_errors     = 0
                overall_passed   = $overallPassed
            }
        }

        $results | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8
        Write-Host "Results written to $OutputPath"

        # CI annotations for failures
        foreach ($pkg in $packages) {
            foreach ($tr in $pkg.test_runs) {
                if ($tr.status -eq 'fail') {
                    Write-CIAnnotation -Level Error -Message "Test failed in $($pkg.path): $($tr.name)"
                }
            }
        }

        # Step summary
        $summaryLines = @()
        $summaryLines += '### Go Test Results'
        $summaryLines += ''
        $summaryLines += '| Package | Passed | Failed | Skipped | Status |'
        $summaryLines += '|---------|--------|--------|---------|--------|'

        foreach ($pkg in $packages) {
            $status = if ($pkg.failed -eq 0) { '✅ Passed' } else { '❌ Failed' }
            $summaryLines += "| $($pkg.path) | $($pkg.passed) | $($pkg.failed) | $($pkg.skipped) | $status |"
        }

        if ($packages.Count -eq 0) {
            $summaryLines += '| (no packages) | 0 | 0 | 0 | ✅ Passed |'
        }

        $summaryLines += ''
        $lintStatus = if ($lintPassed) { '✅ Passed' } else { '❌ Failed' }
        $summaryLines += "**Lint:** $lintStatus | **Total:** $totalPassed passed, $totalFailed failed, $totalSkipped skipped"

        $summaryContent = $summaryLines -join "`n"
        Write-CIStepSummary -Content $summaryContent
        Write-Host $summaryContent
    }
    finally {
        Pop-Location
    }

    if ($overallPassed) { return 0 } else { return 1 }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-GoTestCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-GoTest failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
