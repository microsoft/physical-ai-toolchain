# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    . $PSScriptRoot/../../linting/ConvertTo-JUnitXml.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force

    function New-TestResults {
        param(
            [array]$Modules = @(),
            [int]$TotalPassed = 0,
            [int]$TotalFailed = 0,
            [int]$TotalErrors = 0
        )
        return @{
            timestamp         = '2026-03-19T12:00:00Z'
            terraform_version = '1.14.7'
            modules           = $Modules
            summary           = @{
                modules_tested  = $Modules.Count
                modules_passed  = @($Modules | Where-Object { $_.failed -eq 0 -and $_.errors -eq 0 }).Count
                modules_skipped = 0
                total_passed    = $TotalPassed
                total_failed    = $TotalFailed
                total_errors    = $TotalErrors
                overall_passed  = ($TotalFailed -eq 0 -and $TotalErrors -eq 0)
            }
        } | ConvertTo-Json -Depth 5
    }
}

Describe 'ConvertTo-JUnitXmlCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestInputPath = Join-Path $TestDrive 'logs/terraform-test-results.json'
        $script:TestOutputPath = Join-Path $TestDrive 'logs/terraform-test-results.xml'

        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'logs') | Out-Null

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'input validation' {
        It 'Returns 1 when input file does not exist' {
            $result = ConvertTo-JUnitXmlCore -InputPath (Join-Path $TestDrive 'nonexistent.json') `
                -OutputPath $script:TestOutputPath
            $result | Should -Be 1
        }

        It 'Writes warning annotation when input file missing' {
            ConvertTo-JUnitXmlCore -InputPath (Join-Path $TestDrive 'nonexistent.json') `
                -OutputPath $script:TestOutputPath
            Should -Invoke Write-CIAnnotation -Times 1 -ParameterFilter {
                $Level -eq 'Warning'
            }
        }

        It 'Does not create output file when input missing' {
            ConvertTo-JUnitXmlCore -InputPath (Join-Path $TestDrive 'nonexistent.json') `
                -OutputPath $script:TestOutputPath
            $script:TestOutputPath | Should -Not -Exist
        }
    }

    Context 'empty results' {
        BeforeEach {
            New-TestResults | Set-Content $script:TestInputPath
        }

        It 'Returns 0 with zero modules' {
            $result = ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Creates valid XML with tests="0"' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            $script:TestOutputPath | Should -Exist
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.tests | Should -Be '0'
            $xml.testsuites.failures | Should -Be '0'
            $xml.testsuites.errors | Should -Be '0'
        }
    }

    Context 'passing tests with test_runs' {
        BeforeEach {
            $module = @{
                path      = 'modules/network'
                passed    = 3
                failed    = 0
                errors    = 0
                skipped   = $false
                test_runs = @(
                    @{ name = 'naming_convention'; status = 'pass' }
                    @{ name = 'subnet_allocation'; status = 'pass' }
                    @{ name = 'nsg_rules'; status = 'pass' }
                )
            }
            New-TestResults -Modules @($module) -TotalPassed 3 | Set-Content $script:TestInputPath
        }

        It 'Returns 0' {
            $result = ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Creates testcase elements with real names' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $testcases = $xml.testsuites.testsuite.testcase
            $testcases.Count | Should -Be 3
            $testcases[0].name | Should -Be 'naming_convention'
            $testcases[1].name | Should -Be 'subnet_allocation'
            $testcases[2].name | Should -Be 'nsg_rules'
        }

        It 'Sets classname to module path' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.testcase[0].classname | Should -Be 'modules/network'
        }

        It 'Sets time attribute on testcase elements' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.testcase[0].time | Should -Be '0'
        }
    }

    Context 'failing tests with test_runs' {
        BeforeEach {
            $module = @{
                path      = 'modules/storage'
                passed    = 1
                failed    = 1
                errors    = 0
                skipped   = $false
                test_runs = @(
                    @{ name = 'valid_config'; status = 'pass' }
                    @{ name = 'encryption_check'; status = 'fail' }
                )
            }
            New-TestResults -Modules @($module) -TotalPassed 1 -TotalFailed 1 |
                Set-Content $script:TestInputPath
        }

        It 'Adds failure element to failed testcase' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $failedCase = $xml.testsuites.testsuite.testcase | Where-Object { $_.name -eq 'encryption_check' }
            $failedCase.failure | Should -Not -BeNullOrEmpty
            $failedCase.failure.message | Should -Be 'Test failed: encryption_check'
        }

        It 'Does not add failure to passing testcase' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $passedCase = $xml.testsuites.testsuite.testcase | Where-Object { $_.name -eq 'valid_config' }
            $passedCase.ChildNodes | Where-Object { $_.Name -eq 'failure' } | Should -BeNullOrEmpty
        }
    }

    Context 'error tests with test_runs' {
        BeforeEach {
            $module = @{
                path      = 'modules/identity'
                passed    = 0
                failed    = 0
                errors    = 1
                skipped   = $false
                test_runs = @(
                    @{ name = 'role_assignment'; status = 'error' }
                )
            }
            New-TestResults -Modules @($module) -TotalErrors 1 | Set-Content $script:TestInputPath
        }

        It 'Adds error element to errored testcase' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $errorCase = $xml.testsuites.testsuite.testcase | Where-Object { $_.name -eq 'role_assignment' }
            $errorCase.error | Should -Not -BeNullOrEmpty
            $errorCase.error.message | Should -Be 'Test error: role_assignment'
        }
    }

    Context 'missing case evidence' {
        BeforeEach {
            $module = @{
                path    = 'modules/compute'
                passed  = 2
                failed  = 1
                errors  = 1
                skipped = $false
            }
            New-TestResults -Modules @($module) -TotalPassed 2 -TotalFailed 1 -TotalErrors 1 |
                Set-Content $script:TestInputPath
        }

        It 'Rejects counters without real test runs rather than inventing passed cases' {
            { ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath } | Should -Throw
        }
    }

    Context 'Go and skipped Terraform reports' {
        It 'Converts Go packages with durations and skipped cases' {
            @{
                timestamp = '2026-03-19T12:00:00Z'
                packages = @(@{
                    path = 'example/tests'
                    passed = 1; failed = 0; errors = 0; skipped = 1
                    test_runs = @(
                        @{ name = 'TestOne'; status = 'pass'; elapsed = 0.25 }
                        @{ name = 'TestTwo'; status = 'skip'; elapsed = 0 }
                    )
                })
            } | ConvertTo-Json -Depth 6 | Set-Content $script:TestInputPath
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath -OutputPath $script:TestOutputPath | Should -Be 0
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.name | Should -Be 'Go Tests'
            $xml.testsuites.tests | Should -Be '2'
            $xml.testsuites.skipped | Should -Be '1'
            $xml.testsuites.testsuite.testcase[0].time | Should -Be '0.25'
            $xml.SelectNodes('//testcase/skipped').Count | Should -Be 1
        }

        It 'Keeps duplicate run names in different Terraform files distinct' {
            $module = @{
                path = 'modules/network'; passed = 1; failed = 0; errors = 0; skipped = 1
                test_runs = @(
                    @{ name = 'naming'; file = 'tests/a.tftest.hcl'; status = 'pass' }
                    @{ name = 'naming'; file = 'tests/b.tftest.hcl'; status = 'skip' }
                )
            }
            New-TestResults -Modules @($module) -TotalPassed 1 | Set-Content $script:TestInputPath
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath -OutputPath $script:TestOutputPath | Should -Be 0
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.tests | Should -Be '2'
            $xml.testsuites.testsuite.skipped | Should -Be '1'
            $xml.testsuites.testsuite.testcase[0].classname | Should -Not -Be $xml.testsuites.testsuite.testcase[1].classname
        }
    }

    Context 'XML structure and attributes' {
        BeforeEach {
            $module = @{
                path      = 'modules/aks'
                passed    = 2
                failed    = 0
                errors    = 0
                skipped   = $false
                test_runs = @(
                    @{ name = 'cluster_config'; status = 'pass' }
                    @{ name = 'node_pool'; status = 'pass' }
                )
            }
            New-TestResults -Modules @($module) -TotalPassed 2 | Set-Content $script:TestInputPath
        }

        It 'Sets time attribute on testsuites element' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.time | Should -Be '0'
        }

        It 'Sets testsuites name to Terraform Tests' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.name | Should -Be 'Terraform Tests'
        }

        It 'Sets skipped attribute on testsuite element' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.skipped | Should -Be '0'
        }

        It 'Sets timestamp attribute on testsuite element' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.timestamp | Should -Be '2026-03-19T12:00:00'
        }

        It 'Sets time attribute on testsuite element' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.time | Should -Be '0'
        }

        It 'Sets testsuite name to module path' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.name | Should -Be 'modules/aks'
        }
    }

    Context 'multiple modules' {
        BeforeEach {
            $modules = @(
                @{
                    path      = 'modules/network'
                    passed    = 2
                    failed    = 0
                    errors    = 0
                    skipped   = $false
                    test_runs = @(
                        @{ name = 'vnet_config'; status = 'pass' }
                        @{ name = 'subnet_config'; status = 'pass' }
                    )
                }
                @{
                    path      = 'modules/storage'
                    passed    = 1
                    failed    = 1
                    errors    = 0
                    skipped   = $false
                    test_runs = @(
                        @{ name = 'account_setup'; status = 'pass' }
                        @{ name = 'access_policy'; status = 'fail' }
                    )
                }
            )
            New-TestResults -Modules $modules -TotalPassed 3 -TotalFailed 1 |
                Set-Content $script:TestInputPath
        }

        It 'Creates one testsuite per module' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.testsuite.Count | Should -Be 2
        }

        It 'Sets correct totals on testsuites element' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $xml.testsuites.tests | Should -Be '4'
            $xml.testsuites.failures | Should -Be '1'
        }

        It 'Sets correct counts per testsuite' {
            ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath `
                -OutputPath $script:TestOutputPath
            [xml]$xml = Get-Content $script:TestOutputPath -Raw
            $networkSuite = $xml.testsuites.testsuite | Where-Object { $_.name -eq 'modules/network' }
            $networkSuite.tests | Should -Be '2'
            $networkSuite.failures | Should -Be '0'
        }
    }

    Context 'output directory creation' {
        It 'Creates output directory when it does not exist' {
            $nestedOutput = Join-Path $TestDrive 'nested/dir/results.xml'
            $module = @{
                path      = 'modules/test'
                passed    = 1
                failed    = 0
                errors    = 0
                skipped   = $false
                test_runs = @(@{ name = 'basic'; status = 'pass' })
            }
            New-TestResults -Modules @($module) -TotalPassed 1 | Set-Content $script:TestInputPath

            $result = ConvertTo-JUnitXmlCore -InputPath $script:TestInputPath -OutputPath $nestedOutput
            $result | Should -Be 0
            $nestedOutput | Should -Exist
        }
    }

    Context 'default paths' {
        It 'Uses default paths when parameters not specified' {
            $defaultInput = Join-Path $TestDrive 'logs/terraform-test-results.json'
            $defaultOutput = Join-Path $TestDrive 'logs/terraform-test-results.xml'
            New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'logs') | Out-Null

            $module = @{
                path      = 'modules/test'
                passed    = 1
                failed    = 0
                errors    = 0
                skipped   = $false
                test_runs = @(@{ name = 'default_test'; status = 'pass' })
            }
            New-TestResults -Modules @($module) -TotalPassed 1 | Set-Content $defaultInput

            $result = ConvertTo-JUnitXmlCore
            $result | Should -Be 0
            $defaultOutput | Should -Exist
        }
    }
}
