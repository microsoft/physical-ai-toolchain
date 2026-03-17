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
                return "infrastructure/terraform/main.tf`ninfrastructure/terraform/vpn/variables.tf"
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
