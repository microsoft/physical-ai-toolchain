#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Converts Go or Terraform test JSON results to JUnit XML format.
.DESCRIPTION
    Reads JSON output from Invoke-GoTest.ps1 or Invoke-TerraformTest.ps1 and
    produces JUnit XML with actual case identities, durations, and skips.
.PARAMETER InputPath
    Path to the JSON results file. Defaults to logs/terraform-test-results.json.
.PARAMETER OutputPath
    Path for the JUnit XML output. Defaults to logs/terraform-test-results.xml.
#>

[CmdletBinding()]
param(
    [string]$InputPath,
    [string]$OutputPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "../lib/Modules/CIHelpers.psm1") -Force

function ConvertTo-JUnitXmlCore {
    [CmdletBinding()]
    param(
        [string]$InputPath,
        [string]$OutputPath
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $InputPath) { $InputPath = Join-Path $repoRoot 'logs/terraform-test-results.json' }
    if (-not $OutputPath) { $OutputPath = Join-Path $repoRoot 'logs/terraform-test-results.xml' }

    if (-not (Test-Path $InputPath)) {
        Write-Warning "Test results not found at $InputPath"
        Write-CIAnnotation -Level Warning -Message "Test results not found at $InputPath"
        return 1
    }

    $results = Get-Content $InputPath -Raw | ConvertFrom-Json
    if ($results.PSObject.Properties['packages']) {
        $suites = @($results.packages)
        $suiteName = 'Go Tests'
    }
    elseif ($results.PSObject.Properties['modules']) {
        $suites = @($results.modules)
        $suiteName = 'Terraform Tests'
    }
    else {
        throw 'Test results must contain modules or packages'
    }
    $timestamp = ([datetime]$results.timestamp).ToString('yyyy-MM-ddTHH:mm:ss')
    $totals = @{ tests = 0; failures = 0; errors = 0; skipped = 0; time = 0.0 }
    $identities = [System.Collections.Generic.HashSet[string]]::new()

    $xml = [System.Xml.XmlDocument]::new()
    $declaration = $xml.CreateXmlDeclaration('1.0', 'UTF-8', $null)
    $xml.AppendChild($declaration) | Out-Null

    $testsuites = $xml.CreateElement('testsuites')
    $testsuites.SetAttribute('name', $suiteName)
    $xml.AppendChild($testsuites) | Out-Null

    foreach ($module in $suites) {
        if (-not $module.PSObject.Properties['test_runs']) {
            throw "Missing test case evidence for $($module.path)"
        }
        $counts = @{ tests = 0; failures = 0; errors = 0; skipped = 0; time = 0.0 }
        $testsuite = $xml.CreateElement('testsuite')
        $testsuite.SetAttribute('name', $module.path)
        $testsuite.SetAttribute('timestamp', $timestamp)
        $testsuites.AppendChild($testsuite) | Out-Null

        foreach ($run in $module.test_runs) {
            if ($run.status -notin @('pass', 'fail', 'error', 'skip')) {
                throw "Invalid test status: $($run.status)"
            }
            $className = $module.path
            if ($run.PSObject.Properties['file'] -and $run.file) {
                $className = "$className/$($run.file)"
            }
            if (-not $run.name -or -not $identities.Add("$className`0$($run.name)")) {
                throw "Missing or duplicate testcase identity in $className"
            }
            $elapsed = if ($run.PSObject.Properties['elapsed']) { [double]$run.elapsed } else { 0.0 }
            if (-not [double]::IsFinite($elapsed) -or $elapsed -lt 0) {
                throw "Invalid testcase duration in $className"
            }
            $testcase = $xml.CreateElement('testcase')
            $testcase.SetAttribute('classname', $className)
            $testcase.SetAttribute('name', $run.name)
            $testcase.SetAttribute('time', $elapsed.ToString([System.Globalization.CultureInfo]::InvariantCulture))
            $counts.tests++
            $counts.time += $elapsed
            if ($run.status -eq 'fail') {
                $counts.failures++
                $failure = $xml.CreateElement('failure')
                $failure.SetAttribute('message', "Test failed: $($run.name)")
                $testcase.AppendChild($failure) | Out-Null
            }
            elseif ($run.status -eq 'error') {
                $counts.errors++
                $errorEl = $xml.CreateElement('error')
                $errorEl.SetAttribute('message', "Test error: $($run.name)")
                $testcase.AppendChild($errorEl) | Out-Null
            }
            elseif ($run.status -eq 'skip') {
                $counts.skipped++
                $testcase.AppendChild($xml.CreateElement('skipped')) | Out-Null
            }
            $testsuite.AppendChild($testcase) | Out-Null
        }
        foreach ($key in $counts.Keys) {
            $testsuite.SetAttribute($key, ([double]$counts[$key]).ToString([System.Globalization.CultureInfo]::InvariantCulture))
            $totals[$key] += $counts[$key]
        }
    }
    foreach ($key in $totals.Keys) {
        $testsuites.SetAttribute($key, ([double]$totals[$key]).ToString([System.Globalization.CultureInfo]::InvariantCulture))
    }

    $outputDir = Split-Path $OutputPath -Parent
    if ($outputDir -and -not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }

    $xml.Save($OutputPath)
    Write-Host "JUnit XML written to $OutputPath"
    return 0
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = ConvertTo-JUnitXmlCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "ConvertTo-JUnitXml failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
