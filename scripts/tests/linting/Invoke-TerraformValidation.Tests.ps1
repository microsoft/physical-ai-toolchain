# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-TerraformValidation.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function terraform { }
}

Describe 'Invoke-TerraformValidationCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/terraform-validation-results.json'
        $script:TestTerraformDir = Join-Path $TestDrive 'infrastructure/terraform'

        # Create all deployment directories so Push-Location works
        foreach ($sub in @('.', 'vpn', 'dns', 'automation')) {
            $dirPath = if ($sub -eq '.') { $script:TestTerraformDir } else { Join-Path $script:TestTerraformDir $sub }
            New-Item -ItemType Directory -Force -Path $dirPath | Out-Null
        }

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}

        # Default: all terraform commands succeed
        Mock terraform {
            $global:LASTEXITCODE = 0
            return '{"terraform_version":"1.9.0"}'
        } -ParameterFilter { $args[0] -eq 'version' }

        Mock terraform {
            $global:LASTEXITCODE = 0
            return ''
        } -ParameterFilter { $args[0] -eq 'fmt' }

        Mock terraform {
            $global:LASTEXITCODE = 0
            return ''
        } -ParameterFilter { $args[0] -eq 'init' }

        Mock terraform {
            $global:LASTEXITCODE = 0
            return '{"valid":true,"error_count":0,"warning_count":0,"diagnostics":[]}'
        } -ParameterFilter { $args[0] -eq 'validate' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when terraform is not in PATH' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform' }
            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes error annotation when terraform missing' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform' }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -Times 1 -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*terraform*not*'
            }
        }
    }

    Context 'clean run' {
        It 'Returns 0 when fmt and all validations pass' {
            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 0
        }

        It 'Creates output JSON file' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $script:TestOutputPath | Should -Exist
        }

        It 'JSON has correct structure' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.format_check | Should -Not -BeNullOrEmpty
            $json.validation | Should -Not -BeNullOrEmpty
            $json.summary | Should -Not -BeNullOrEmpty
        }

        It 'summary.overall_passed is true' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.overall_passed | Should -BeTrue
            $json.summary.format_passed | Should -BeTrue
        }

        It 'Step summary is written' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'format check failures' {
        BeforeEach {
            Mock terraform {
                $global:LASTEXITCODE = 1
                return @('infrastructure/terraform/main.tf', 'infrastructure/terraform/vpn/variables.tf')
            } -ParameterFilter { $args[0] -eq 'fmt' }
        }

        It 'Returns 1 when fmt fails' {
            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes warning annotations for unformatted files' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Warning' -and $Message -like '*not formatted*'
            }
        }

        It 'Captures unformatted files in JSON output' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.format_check.unformatted_files | Should -Not -BeNullOrEmpty
        }

        It 'format_check.passed is false in output' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.format_check.passed | Should -BeFalse
        }
    }

    Context 'validation errors' {
        BeforeEach {
            Mock terraform {
                $global:LASTEXITCODE = 1
                return '{"valid":false,"error_count":1,"warning_count":0,"diagnostics":[{"severity":"error","summary":"Missing resource","detail":"Resource not found","range":{"filename":"main.tf","start":{"line":10,"column":1},"end":{"line":10,"column":20}}}]}'
            } -ParameterFilter { $args[0] -eq 'validate' }
        }

        It 'Returns 1 when validate fails for a directory' {
            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes error annotations for validation errors' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -eq 'Missing resource'
            }
        }

        It 'Captures errors in JSON' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $failed = $json.validation | Where-Object { -not $_.passed }
            $failed | Should -Not -BeNullOrEmpty
            ($failed | Select-Object -First 1).errors.Count | Should -BeGreaterThan 0
        }

        It 'Failed directory shows passed=false' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.validation | ForEach-Object { $_.passed | Should -BeFalse }
        }
    }

    Context 'validation warnings' {
        BeforeEach {
            Mock terraform {
                $global:LASTEXITCODE = 0
                return '{"valid":true,"error_count":0,"warning_count":1,"diagnostics":[{"severity":"warning","summary":"Deprecated attribute","detail":"This attribute is deprecated","range":{"filename":"main.tf","start":{"line":5,"column":1},"end":{"line":5,"column":15}}}]}'
            } -ParameterFilter { $args[0] -eq 'validate' }
        }

        It 'Writes warning annotations for validation warnings' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Warning' -and $Message -eq 'Deprecated attribute'
            }
        }

        It 'Captures warnings in JSON' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $withWarnings = $json.validation | Where-Object { $_.warnings.Count -gt 0 }
            $withWarnings | Should -Not -BeNullOrEmpty
        }
    }

    Context 'optional <Severity> diagnostic fields' -ForEach @(
        @{ Severity = 'error'; ExitCode = 1; AnnotationLevel = 'Error'; Bucket = 'errors' }
        @{ Severity = 'warning'; ExitCode = 0; AnnotationLevel = 'Warning'; Bucket = 'warnings' }
    ) {
        BeforeEach {
            $ErrorActionPreference = 'Stop'
            Mock Write-Host {}
        }

        It 'Reports a diagnostic with <Name>' -ForEach @(
            @{ Name = 'no range'; Fields = @{}; HasFile = $false; ExpectedLine = 0; HasDetail = $true }
            @{ Name = 'null range'; Fields = @{ range = $null }; HasFile = $false; ExpectedLine = 0; HasDetail = $true }
            @{ Name = 'empty range'; Fields = @{ range = @{} }; HasFile = $false; ExpectedLine = 0; HasDetail = $true }
            @{ Name = 'no filename'; Fields = @{ range = @{ start = @{ line = 12 } } }; HasFile = $false; ExpectedLine = 12; HasDetail = $true }
            @{ Name = 'no start'; Fields = @{ range = @{ filename = 'main.tf' } }; HasFile = $true; ExpectedLine = 0; HasDetail = $true }
            @{ Name = 'empty start'; Fields = @{ range = @{ filename = 'main.tf'; start = @{} } }; HasFile = $true; ExpectedLine = 0; HasDetail = $true }
            @{ Name = 'no line'; Fields = @{ range = @{ filename = 'main.tf'; start = @{ column = 1 } } }; HasFile = $true; ExpectedLine = 0; HasDetail = $true }
            @{ Name = 'no detail'; Fields = @{ range = @{ filename = 'main.tf'; start = @{ line = 12 } } }; HasFile = $true; ExpectedLine = 12; HasDetail = $false }
        ) {
            $diagnostic = @{ severity = $Severity; summary = 'Diagnostic summary' } + $Fields
            if ($HasDetail) { $diagnostic.detail = 'Diagnostic detail' }
            $expectedAnnotationLine = if ($ExpectedLine -gt 0) { $ExpectedLine } else { $null }
            $script:ValidateResponse = @{ diagnostics = @($diagnostic) } | ConvertTo-Json -Depth 10
            $script:ValidateExitCode = $ExitCode
            Mock terraform {
                $global:LASTEXITCODE = $script:ValidateExitCode
                return $script:ValidateResponse
            } -ParameterFilter { $args[0] -eq 'validate' }

            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir

            $result | Should -Be $ExitCode
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json -AsHashtable
            $json.validation | Should -HaveCount 4
            foreach ($directory in $json.validation) {
                $directory.passed | Should -Be ($ExitCode -eq 0)
                $directory.skipped | Should -BeFalse
                $directory[$Bucket] | Should -HaveCount 1
                $entry = $directory[$Bucket][0]
                $entry.severity | Should -Be $Severity
                $entry.summary | Should -Be 'Diagnostic summary'
                $entry.line | Should -Be $ExpectedLine
                if ($HasFile) {
                    $expectedFile = "$($directory.directory)/main.tf"
                    $entry.file | Should -Be $expectedFile
                    Should -Invoke Write-CIAnnotation -Times 1 -Exactly -ParameterFilter {
                        $Level -eq $AnnotationLevel -and $Message -eq 'Diagnostic summary' -and
                        $File -eq $expectedFile -and $Line -eq $expectedAnnotationLine
                    }
                }
                else {
                    $entry.file | Should -BeNullOrEmpty
                }
                if ($HasDetail) { $entry.detail | Should -Be 'Diagnostic detail' }
                else { $entry.detail | Should -BeNullOrEmpty }
                $otherBucket = if ($Bucket -eq 'errors') { 'warnings' } else { 'errors' }
                $directory[$otherBucket] | Should -HaveCount 0
            }
            Should -Invoke Write-CIAnnotation -Times 4 -Exactly -ParameterFilter {
                $Level -eq $AnnotationLevel -and $Message -eq 'Diagnostic summary' -and $Line -eq $expectedAnnotationLine
            }
            if (-not $HasFile) {
                Should -Invoke Write-CIAnnotation -Times 0 -Exactly -ParameterFilter { $File }
            }
            $detailCalls = if ($HasDetail) { 4 } else { 0 }
            Should -Invoke Write-Host -Times $detailCalls -Exactly -ParameterFilter { $Object -eq 'Diagnostic detail' }
            $json.summary.directories_checked | Should -Be 4
            $json.summary.directories_passed | Should -Be $(if ($ExitCode -eq 0) { 4 } else { 0 })
            $json.summary.overall_passed | Should -Be ($ExitCode -eq 0)
            Should -Invoke Write-CIStepSummary -Times 1 -Exactly
        }
    }

    Context 'initialization failures' {
        It 'Completes reporting when initialization fails in <FailureMode>' -ForEach @(
            @{ FailureMode = 'all directories'; FailAll = $true; ExpectedPassed = 0 }
            @{ FailureMode = 'only vpn'; FailAll = $false; ExpectedPassed = 3 }
        ) {
            $ErrorActionPreference = 'Stop'
            $originalLocation = (Get-Location).Path
            $script:FailAllInitializations = $FailAll
            $script:ValidatedDirectories = @()
            Mock Write-Host {}
            Mock terraform {
                if ($script:FailAllInitializations -or (Split-Path (Get-Location).Path -Leaf) -eq 'vpn') {
                    $global:LASTEXITCODE = 1
                    return @('Provider installation failed', 'Registry unavailable')
                }
                $global:LASTEXITCODE = 0
                return ''
            } -ParameterFilter { $args[0] -eq 'init' }
            Mock terraform {
                $script:ValidatedDirectories += (Get-Location).Path
                $global:LASTEXITCODE = 0
                return '{"valid":true,"diagnostics":[]}'
            } -ParameterFilter { $args[0] -eq 'validate' }

            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir

            $result | Should -Be 1
            (Get-Location).Path | Should -Be $originalLocation
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.validation | Should -HaveCount 4
            foreach ($directory in $json.validation) {
                $shouldFail = $FailAll -or $directory.directory -like '*/vpn'
                $directory.passed | Should -Be (-not $shouldFail)
                $directory.skipped | Should -BeFalse
                $directory.warnings | Should -HaveCount 0
                if ($shouldFail) {
                    $directory.errors | Should -HaveCount 1
                    $errorEntry = $directory.errors[0]
                    $errorEntry.severity | Should -Be 'error'
                    $errorEntry.summary | Should -Be "Terraform initialization failed: $($directory.directory)"
                    $errorEntry.detail | Should -Be ("Provider installation failed`nRegistry unavailable" -replace "`n", [Environment]::NewLine)
                    $errorEntry.file | Should -BeNullOrEmpty
                    $errorEntry.line | Should -Be 0
                }
                else {
                    $directory.errors | Should -HaveCount 0
                    $script:ValidatedDirectories | Should -Contain (Get-Item $directory.directory).FullName -Because 'successful peer directories must still be validated'
                }
            }
            $script:ValidatedDirectories | Should -Not -Contain (Join-Path $script:TestTerraformDir 'vpn')
            $json.summary.directories_checked | Should -Be 4
            $json.summary.directories_passed | Should -Be $ExpectedPassed
            $json.summary.directories_skipped | Should -Be 0
            $json.summary.overall_passed | Should -BeFalse
            Should -Invoke terraform -Times 4 -Exactly -ParameterFilter { $args[0] -eq 'init' }
            Should -Invoke terraform -Times $ExpectedPassed -Exactly -ParameterFilter { $args[0] -eq 'validate' }
            $expectedFailures = 4 - $ExpectedPassed
            Should -Invoke Write-CIAnnotation -Times $expectedFailures -Exactly -ParameterFilter {
                $Level -eq 'Error' -and $Message -like 'Terraform initialization failed:*' -and $null -eq $File -and $null -eq $Line
            }
            Should -Invoke Write-Host -Times $expectedFailures -Exactly -ParameterFilter {
                $Object -match 'Provider installation failed' -and $Object -match 'Registry unavailable'
            }
            Should -Invoke Write-CIStepSummary -Times 1 -Exactly -ParameterFilter { $Content -match 'Terraform Validation Results' }
        }
    }

    Context 'invalid validation output' {
        It 'Reports <Name> as a failure and continues to later directories' -ForEach @(
            @{ Name = 'malformed JSON'; Output = 'not JSON'; NativeExitCode = 1 }
            @{ Name = 'empty output'; Output = ''; NativeExitCode = 0 }
            @{ Name = 'JSON null'; Output = 'null'; NativeExitCode = 0 }
            @{ Name = 'missing diagnostics'; Output = '{"valid":true,"error_count":0,"warning_count":0}'; NativeExitCode = 0 }
        ) {
            $ErrorActionPreference = 'Stop'
            $originalLocation = (Get-Location).Path
            $script:InvalidValidateOutput = $Output
            $script:InvalidValidateExit = $NativeExitCode
            Mock terraform {
                if ((Split-Path (Get-Location).Path -Leaf) -eq 'vpn') {
                    $global:LASTEXITCODE = $script:InvalidValidateExit
                    return $script:InvalidValidateOutput
                }
                $global:LASTEXITCODE = 0
                return '{"valid":true,"diagnostics":[]}'
            } -ParameterFilter { $args[0] -eq 'validate' }

            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir

            $result | Should -Be 1
            (Get-Location).Path | Should -Be $originalLocation
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.validation | Should -HaveCount 4
            $failed = @($json.validation | Where-Object { -not $_.passed })
            $failed | Should -HaveCount 1
            $failed[0].directory | Should -Be "$script:TestTerraformDir/vpn"
            $failed[0].errors | Should -HaveCount 1
            $entry = $failed[0].errors[0]
            $entry.severity | Should -Be 'error'
            $entry.summary | Should -Be "Invalid Terraform validation output: $script:TestTerraformDir/vpn"
            $entry.detail | Should -Be $Output
            $entry.file | Should -BeNullOrEmpty
            $entry.line | Should -Be 0
            foreach ($directory in $json.validation) {
                $directory.skipped | Should -BeFalse
                $directory.warnings | Should -HaveCount 0
                if ($directory.passed) { $directory.errors | Should -HaveCount 0 }
            }
            $json.summary.directories_checked | Should -Be 4
            $json.summary.directories_passed | Should -Be 3
            $json.summary.overall_passed | Should -BeFalse
            Should -Invoke terraform -Times 4 -Exactly -ParameterFilter { $args[0] -eq 'validate' }
            Should -Invoke Write-CIAnnotation -Times 1 -Exactly -ParameterFilter {
                $Level -eq 'Error' -and $Message -eq "Invalid Terraform validation output: $script:TestTerraformDir/vpn" -and
                $null -eq $File -and $null -eq $Line
            }
            Should -Invoke Write-CIStepSummary -Times 1 -Exactly
        }
    }

    Context 'native error preference' {
        It 'Reports nonzero exits without changing the caller native error preference' {
            $ErrorActionPreference = 'Stop'
            $PSNativeCommandUseErrorActionPreference = $true
            $script:ObservedNativePreferences = @()
            Mock terraform {
                $script:ObservedNativePreferences += $PSNativeCommandUseErrorActionPreference
                $global:LASTEXITCODE = 1
                return 'Provider installation failed'
            } -ParameterFilter { $args[0] -eq 'init' }

            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir

            $result | Should -Be 1
            $script:ObservedNativePreferences | Should -HaveCount 4
            $script:ObservedNativePreferences | ForEach-Object { $_ | Should -BeFalse }
            $PSNativeCommandUseErrorActionPreference | Should -BeTrue
            $ErrorActionPreference | Should -Be 'Stop'
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.directories_checked | Should -Be 4
            $json.summary.overall_passed | Should -BeFalse
            Should -Invoke Write-CIStepSummary -Times 1 -Exactly
        }
    }

    Context 'change detection (ChangedFilesOnly)' {
        BeforeEach {
            Mock Get-ChangedFilesFromGit { return @() }
        }

        It 'Calls Get-ChangedFilesFromGit with correct extensions' {
            Mock Get-ChangedFilesFromGit { return @() }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            Should -Invoke Get-ChangedFilesFromGit -Times 1 -ParameterFilter {
                ($FileExtensions -contains '*.tf') -and ($FileExtensions -contains '*.tfvars')
            }
        }

        It 'Returns 0 early when no terraform files changed' {
            Mock Get-ChangedFilesFromGit { return @() }
            $result = Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $result | Should -Be 0
        }

        It 'Maps root terraform files to dot directory' {
            Mock Get-ChangedFilesFromGit { return @('infrastructure/terraform/main.tf') }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $rootResult = $json.validation | Where-Object { $_.directory -eq $script:TestTerraformDir }
            $rootResult.skipped | Should -BeFalse
        }

        It 'Maps vpn files to vpn directory' {
            Mock Get-ChangedFilesFromGit { return @('infrastructure/terraform/vpn/main.tf') }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $vpnResult = $json.validation | Where-Object { $_.directory -like '*/vpn' }
            $vpnResult.skipped | Should -BeFalse
        }

        It 'Maps dns files to dns directory' {
            Mock Get-ChangedFilesFromGit { return @('infrastructure/terraform/dns/main.tf') }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $dnsResult = $json.validation | Where-Object { $_.directory -like '*/dns' }
            $dnsResult.skipped | Should -BeFalse
        }

        It 'Maps automation files to automation directory' {
            Mock Get-ChangedFilesFromGit { return @('infrastructure/terraform/automation/main.tf') }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $autoResult = $json.validation | Where-Object { $_.directory -like '*/automation' }
            $autoResult.skipped | Should -BeFalse
        }

        It 'Maps modules/ files to root directory' {
            Mock Get-ChangedFilesFromGit { return @('infrastructure/terraform/modules/foo/main.tf') }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $rootResult = $json.validation | Where-Object { $_.directory -eq $script:TestTerraformDir }
            $rootResult.skipped | Should -BeFalse
        }

        It 'Skips directories with no changes' {
            Mock Get-ChangedFilesFromGit { return @('infrastructure/terraform/vpn/main.tf') }
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $skipped = $json.validation | Where-Object { $_.skipped -eq $true }
            $skipped.Count | Should -BeGreaterOrEqual 2
        }
    }

    Context 'output file creation' {
        It 'Creates output directory if it does not exist' {
            $nestedOutput = Join-Path $TestDrive 'deep/nested/output.json'
            Invoke-TerraformValidationCore -OutputPath $nestedOutput `
                -TerraformDir $script:TestTerraformDir
            Split-Path $nestedOutput -Parent | Should -Exist
        }

        It 'Writes valid JSON to output path' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            { Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json } | Should -Not -Throw
        }
    }

    Context 'per-directory validation' {
        It 'Validates all 4 directories by default' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke terraform -Times 4 -ParameterFilter { $args[0] -eq 'init' }
            Should -Invoke terraform -Times 4 -ParameterFilter { $args[0] -eq 'validate' }
        }

        It 'Runs init with -backend=false' {
            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke terraform -ParameterFilter {
                $args[0] -eq 'init' -and ($args -contains '-backend=false')
            }
        }
    }

    Context 'multiple directory results' {
        It 'Mixed results: some pass, some fail' {
            # Override validate mock — fail only when in the vpn directory
            Mock terraform {
                $cwd = (Get-Location).Path
                if ($cwd -like '*vpn*') {
                    $global:LASTEXITCODE = 1
                    return '{"valid":false,"error_count":1,"warning_count":0,"diagnostics":[{"severity":"error","summary":"VPN error","detail":"misconfigured","range":{"filename":"main.tf","start":{"line":1,"column":1},"end":{"line":1,"column":10}}}]}'
                }
                $global:LASTEXITCODE = 0
                return '{"valid":true,"error_count":0,"warning_count":0,"diagnostics":[]}'
            } -ParameterFilter { $args[0] -eq 'validate' }

            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $passed = ($json.validation | Where-Object { $_.passed -eq $true }).Count
            $failed = ($json.validation | Where-Object { $_.passed -eq $false }).Count
            $passed | Should -BeGreaterThan 0
            $failed | Should -BeGreaterThan 0
        }

        It 'overall_passed is false when any directory fails' {
            Mock terraform {
                $cwd = (Get-Location).Path
                if ($cwd -like '*vpn*') {
                    $global:LASTEXITCODE = 1
                    return '{"valid":false,"error_count":1,"warning_count":0,"diagnostics":[{"severity":"error","summary":"Error","detail":"fail","range":{"filename":"main.tf","start":{"line":1,"column":1},"end":{"line":1,"column":5}}}]}'
                }
                $global:LASTEXITCODE = 0
                return '{"valid":true,"error_count":0,"warning_count":0,"diagnostics":[]}'
            } -ParameterFilter { $args[0] -eq 'validate' }

            Invoke-TerraformValidationCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.overall_passed | Should -BeFalse
        }
    }
}
