#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Converts Terraform test JSON results to JUnit XML format.
.DESCRIPTION
    Reads JSON output from Invoke-TerraformTest.ps1 and produces a JUnit XML
    file compatible with Codecov Test Analytics. Includes required attributes
    (time, skipped, timestamp) on all elements and uses real test names from
    the test_runs array when available.
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

Import-Module (Join-Path $PSScriptRoot "../../../shared/lib/Modules/CIHelpers.psm1") -Force

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
    $totalTests = $results.summary.total_passed + $results.summary.total_failed + $results.summary.total_errors
    $timestamp = ([datetime]::Parse($results.timestamp)).ToString('yyyy-MM-ddTHH:mm:ss')

    $xml = [System.Xml.XmlDocument]::new()
    $declaration = $xml.CreateXmlDeclaration('1.0', 'UTF-8', $null)
    $xml.AppendChild($declaration) | Out-Null

    $testsuites = $xml.CreateElement('testsuites')
    $testsuites.SetAttribute('name', 'Terraform Tests')
    $testsuites.SetAttribute('tests', $totalTests)
    $testsuites.SetAttribute('failures', $results.summary.total_failed)
    $testsuites.SetAttribute('errors', $results.summary.total_errors)
    $testsuites.SetAttribute('time', '0')
    $xml.AppendChild($testsuites) | Out-Null

    foreach ($module in $results.modules) {
        $moduleTests = $module.passed + $module.failed + $module.errors
        $testsuite = $xml.CreateElement('testsuite')
        $testsuite.SetAttribute('name', $module.path)
        $testsuite.SetAttribute('tests', $moduleTests)
        $testsuite.SetAttribute('failures', $module.failed)
        $testsuite.SetAttribute('errors', $module.errors)
        $testsuite.SetAttribute('skipped', '0')
        $testsuite.SetAttribute('timestamp', $timestamp)
        $testsuite.SetAttribute('time', '0')
        $testsuites.AppendChild($testsuite) | Out-Null

        $hasTestRuns = ($null -ne $module.PSObject.Properties['test_runs']) -and ($module.test_runs.Count -gt 0)
        if ($hasTestRuns) {
            foreach ($run in $module.test_runs) {
                $testcase = $xml.CreateElement('testcase')
                $testcase.SetAttribute('classname', $module.path)
                $testcase.SetAttribute('name', $run.name)
                $testcase.SetAttribute('time', '0')

                if ($run.status -eq 'fail') {
                    $failure = $xml.CreateElement('failure')
                    $failure.SetAttribute('message', "Test failed: $($run.name)")
                    $testcase.AppendChild($failure) | Out-Null
                }
                elseif ($run.status -eq 'error') {
                    $errorEl = $xml.CreateElement('error')
                    $errorEl.SetAttribute('message', "Test error: $($run.name)")
                    $testcase.AppendChild($errorEl) | Out-Null
                }

                $testsuite.AppendChild($testcase) | Out-Null
            }
        }
        else {
            for ($i = 1; $i -le $module.passed; $i++) {
                $testcase = $xml.CreateElement('testcase')
                $testcase.SetAttribute('classname', $module.path)
                $testcase.SetAttribute('name', "test_$i")
                $testcase.SetAttribute('time', '0')
                $testsuite.AppendChild($testcase) | Out-Null
            }
            for ($i = 1; $i -le $module.failed; $i++) {
                $testcase = $xml.CreateElement('testcase')
                $testcase.SetAttribute('classname', $module.path)
                $testcase.SetAttribute('name', "failed_$i")
                $testcase.SetAttribute('time', '0')
                $failure = $xml.CreateElement('failure')
                $failure.SetAttribute('message', "Test failed in $($module.path)")
                $testcase.AppendChild($failure) | Out-Null
                $testsuite.AppendChild($testcase) | Out-Null
            }
            for ($i = 1; $i -le $module.errors; $i++) {
                $testcase = $xml.CreateElement('testcase')
                $testcase.SetAttribute('classname', $module.path)
                $testcase.SetAttribute('name', "error_$i")
                $testcase.SetAttribute('time', '0')
                $errorEl = $xml.CreateElement('error')
                $errorEl.SetAttribute('message', "Test errored in $($module.path)")
                $testcase.AppendChild($errorEl) | Out-Null
                $testsuite.AppendChild($testcase) | Out-Null
            }
        }
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
