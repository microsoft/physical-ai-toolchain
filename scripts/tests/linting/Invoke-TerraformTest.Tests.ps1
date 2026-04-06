# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-TerraformTest.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function terraform { }
}

Describe 'Invoke-TerraformTestCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/terraform-test-results.json'
        $script:TestTerraformDir = Join-Path $TestDrive 'infrastructure/terraform'

        New-Item -ItemType Directory -Force -Path $script:TestTerraformDir | Out-Null

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}

        Mock terraform {
            $global:LASTEXITCODE = 0
            return '{"terraform_version":"1.14.7"}'
        } -ParameterFilter { $args[0] -eq 'version' }

        Mock terraform {
            $global:LASTEXITCODE = 0
            return ''
        } -ParameterFilter { $args[0] -eq 'init' }

        Mock terraform {
            $global:LASTEXITCODE = 0
            return ''
        } -ParameterFilter { $args[0] -eq 'test' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when terraform is not in PATH' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform' }
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes error annotation when terraform missing' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform' }
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -Times 1 -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*terraform*not*'
            }
        }
    }

    Context 'no test directories' {
        It 'Returns 0 when no tests/ directories exist' {
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 0
        }

        It 'Creates output JSON file' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $script:TestOutputPath | Should -Exist
        }

        It 'Reports 0 modules tested in JSON' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.modules_tested | Should -Be 0
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Step summary is written' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'single module with tests' {
        BeforeEach {
            $modulePath = Join-Path $script:TestTerraformDir 'modules/platform'
            $testsPath = Join-Path $modulePath 'tests'
            New-Item -ItemType Directory -Force -Path $testsPath | Out-Null

            Mock terraform {
                $global:LASTEXITCODE = 0
                @(
                    '{"type":"test_run","test_run":{"run":"naming","progress":"complete","status":"pass"}}'
                    '{"type":"test_run","test_run":{"run":"conditionals","progress":"complete","status":"pass"}}'
                    '{"type":"test_summary","test_summary":{"status":"pass","passed":2,"failed":0,"errored":0,"skipped":0}}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Returns 0 when all tests pass' {
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 0
        }

        It 'JSON reports correct pass count' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.modules_tested | Should -Be 1
            $json.summary.total_passed | Should -Be 2
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Runs terraform init before test' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke terraform -Times 1 -ParameterFilter { $args[0] -eq 'init' }
        }
    }

    Context 'test failure' {
        BeforeEach {
            $modulePath = Join-Path $script:TestTerraformDir 'modules/platform'
            $testsPath = Join-Path $modulePath 'tests'
            New-Item -ItemType Directory -Force -Path $testsPath | Out-Null

            Mock terraform {
                $global:LASTEXITCODE = 1
                @(
                    '{"type":"test_run","test_run":{"run":"naming","progress":"complete","status":"pass"}}'
                    '{"type":"test_run","test_run":{"run":"conditionals","progress":"complete","status":"fail"}}'
                    '{"type":"test_summary","test_summary":{"status":"fail","passed":1,"failed":1,"errored":0,"skipped":0}}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Returns 1 when any test fails' {
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes error annotation for failed test' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*failed*conditionals*'
            }
        }

        It 'JSON reports correct fail count' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.total_passed | Should -Be 1
            $json.summary.total_failed | Should -Be 1
            $json.summary.overall_passed | Should -BeFalse
        }
    }

    Context 'ChangedFilesOnly' {
        BeforeEach {
            $modulePath = Join-Path $script:TestTerraformDir 'modules/platform'
            $testsPath = Join-Path $modulePath 'tests'
            New-Item -ItemType Directory -Force -Path $testsPath | Out-Null

            $modulePath2 = Join-Path $script:TestTerraformDir 'modules/sil'
            $testsPath2 = Join-Path $modulePath2 'tests'
            New-Item -ItemType Directory -Force -Path $testsPath2 | Out-Null

            Mock terraform {
                $global:LASTEXITCODE = 0
                @(
                    '{"type":"test_run","test_run":{"run":"naming","progress":"complete","status":"pass"}}'
                    '{"type":"test_summary","test_summary":{"status":"pass","passed":1,"failed":0,"errored":0,"skipped":0}}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Returns 0 early when no terraform files changed' {
            Mock Get-ChangedFilesFromGit { return @() }
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $result | Should -Be 0
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.modules_tested | Should -Be 0
        }

        It 'Calls Get-ChangedFilesFromGit with correct extensions' {
            Mock Get-ChangedFilesFromGit { return @() }
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            Should -Invoke Get-ChangedFilesFromGit -Times 1 -ParameterFilter {
                ($FileExtensions -contains '*.tf') -and ($FileExtensions -contains '*.tftest.hcl')
            }
        }

        It 'Only tests modules with changed files' {
            Mock Get-ChangedFilesFromGit {
                return @('infrastructure/terraform/modules/platform/main.tf')
            }
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.modules_tested | Should -Be 1
            $json.modules[0].path | Should -BeLike '*platform*'
        }
    }

    Context 'output file creation' {
        It 'Creates output directory if it does not exist' {
            $nestedOutput = Join-Path $TestDrive 'deep/nested/output.json'
            Invoke-TerraformTestCore -OutputPath $nestedOutput `
                -TerraformDir $script:TestTerraformDir
            Split-Path $nestedOutput -Parent | Should -Exist
        }

        It 'Writes valid JSON to output path' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            { Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json } | Should -Not -Throw
        }
    }

    Context 'init failure' {
        BeforeEach {
            $modulePath = Join-Path $script:TestTerraformDir 'modules/platform'
            $testsPath = Join-Path $modulePath 'tests'
            New-Item -ItemType Directory -Force -Path $testsPath | Out-Null

            Mock terraform {
                $global:LASTEXITCODE = 1
                return 'Error: Failed to init'
            } -ParameterFilter { $args[0] -eq 'init' }
        }

        It 'Reports error when init fails' {
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes error annotation for init failure' {
            Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*init failed*'
            }
        }
    }

    Context 'standalone deployment with tests' {
        BeforeEach {
            $standaloneDir = Join-Path $script:TestTerraformDir 'vpn'
            $testsPath = Join-Path $standaloneDir 'tests'
            New-Item -ItemType Directory -Force -Path $testsPath | Out-Null

            Mock terraform {
                $global:LASTEXITCODE = 0
                @(
                    '{"type":"test_run","test_run":{"run":"vpn_test","progress":"complete","status":"pass"}}'
                    '{"type":"test_summary","test_summary":{"status":"pass","passed":1,"failed":0,"errored":0,"skipped":0}}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Discovers standalone deployment test directories' {
            $result = Invoke-TerraformTestCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 0
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.modules_tested | Should -Be 1
        }
    }
}
