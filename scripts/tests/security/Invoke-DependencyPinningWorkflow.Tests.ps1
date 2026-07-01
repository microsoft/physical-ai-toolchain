#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../security/Invoke-DependencyPinningWorkflow.ps1

    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
}

Describe 'Invoke-DependencyPinningWorkflowCore' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        Push-Location $TestDrive
        Remove-Item -Path logs -Recurse -Force -ErrorAction SilentlyContinue
    }

    AfterEach {
        Pop-Location
        Remove-MockCIFiles -MockFiles $script:MockFiles
        Restore-CIEnvironment
    }

    It 'Writes outputs and summary for a compliant report' {
        $scanner = {
            param([hashtable]$ScannerParams, [ref]$ExitCode)

            New-Item -ItemType Directory -Force -Path (Split-Path $ScannerParams.OutputPath) | Out-Null
            @{
                ComplianceScore     = 100
                UnpinnedDependencies = 0
                Violations          = @()
            } | ConvertTo-Json -Depth 5 | Set-Content -Path $ScannerParams.OutputPath
            $ExitCode.Value = 0
        }

        $result = Invoke-DependencyPinningWorkflowCore -Threshold 95 -UploadSarif 'false' -InvokeScanner $scanner

        $result | Should -Be 0
        Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'compliance-score=100'
        Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'is-compliant=true'
        Get-Content $env:GITHUB_STEP_SUMMARY -Raw | Should -Match 'All Dependencies Pinned'
    }

    It 'Returns SARIF scanner failures before uploading missing SARIF' {
        $scanner = {
            param([hashtable]$ScannerParams, [ref]$ExitCode)

            if ($ScannerParams.Format -eq 'sarif') {
                $ExitCode.Value = 9
                return
            }

            New-Item -ItemType Directory -Force -Path (Split-Path $ScannerParams.OutputPath) | Out-Null
            @{
                ComplianceScore     = 100
                UnpinnedDependencies = 0
                Violations          = @()
            } | ConvertTo-Json -Depth 5 | Set-Content -Path $ScannerParams.OutputPath
            $ExitCode.Value = 0
        }

        Invoke-DependencyPinningWorkflowCore -Threshold 95 -UploadSarif 'true' -InvokeScanner $scanner | Should -Be 9
    }

    It 'Fails hard when compliance is below threshold and soft-fail is disabled' {
        $scanner = {
            param([hashtable]$ScannerParams, [ref]$ExitCode)

            New-Item -ItemType Directory -Force -Path (Split-Path $ScannerParams.OutputPath) | Out-Null
            @{
                ComplianceScore     = 80
                UnpinnedDependencies = 1
                Violations          = @(
                    @{
                        File     = 'package.json'
                        Line     = 3
                        Type     = 'npm'
                        Name     = 'left-pad'
                        Version  = '^1.3.0'
                        Severity = 'warning'
                    }
                )
            } | ConvertTo-Json -Depth 5 | Set-Content -Path $ScannerParams.OutputPath
            $ExitCode.Value = 0
        }

        Invoke-DependencyPinningWorkflowCore -Threshold 95 -SoftFail 'false' -UploadSarif 'false' -InvokeScanner $scanner | Should -Be 1
        Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'is-compliant=false'
    }

    It 'Propagates JSON scanner exit codes' {
        $scanner = {
            param([hashtable]$ScannerParams, [ref]$ExitCode)

            New-Item -ItemType Directory -Force -Path (Split-Path $ScannerParams.OutputPath) | Out-Null
            @{
                ComplianceScore     = 100
                UnpinnedDependencies = 0
                Violations          = @()
            } | ConvertTo-Json -Depth 5 | Set-Content -Path $ScannerParams.OutputPath
            $ExitCode.Value = 7
        }

        Invoke-DependencyPinningWorkflowCore -Threshold 95 -UploadSarif 'false' -InvokeScanner $scanner | Should -Be 7
    }

    It 'Rejects thresholds outside the allowed range' {
        { Invoke-DependencyPinningWorkflowCore -Threshold 101 -InvokeScanner { param($ScannerParams, [ref]$ExitCode) $ExitCode.Value = 0 } } |
            Should -Throw -ExpectedMessage 'Threshold must be between 0 and 100; got 101'
    }

    It 'Fails when the scanner does not write a JSON report' {
        $scanner = {
            param($ScannerParams, [ref]$ExitCode)

            $ExitCode.Value = 0
        }

        { Invoke-DependencyPinningWorkflowCore -Threshold 95 -UploadSarif 'false' -InvokeScanner $scanner } |
            Should -Throw -ExpectedMessage 'Failed to generate dependency pinning report'
    }
}
