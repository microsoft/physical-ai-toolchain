# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

# Stub functions for external tools trigger PSUseApprovedVerbs
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '')]
param()

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-GoTest.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function go { }
    function golangci-lint { }
}

Describe 'Invoke-GoTestCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/go-test-results.json'
        $script:TestCoveragePath = Join-Path $TestDrive 'logs/go-coverage.out'
        $script:TestGoDir = Join-Path $TestDrive 'infrastructure/terraform/e2e'
        New-Item -ItemType Directory -Force -Path $script:TestGoDir | Out-Null
        New-Item -ItemType File -Force -Path (Join-Path $script:TestGoDir 'go.mod') | Out-Null

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}

        Mock go {
            $global:LASTEXITCODE = 0
            return 'go version go1.26 linux/amd64'
        } -ParameterFilter { $args[0] -eq 'version' }

        Mock golangci-lint {
            $global:LASTEXITCODE = 0
            return 'golangci-lint has version v2.11.4'
        } -ParameterFilter { $args[0] -eq 'version' }

        Mock golangci-lint {
            $global:LASTEXITCODE = 0
            return ''
        } -ParameterFilter { $args[0] -eq 'run' }

        Mock go {
            $global:LASTEXITCODE = 0
            return ''
        } -ParameterFilter { $args[0] -eq 'test' }

        Mock go {
            $global:LASTEXITCODE = 0
            return '/usr/local/go/bin'
        } -ParameterFilter { $args[0] -eq 'env' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when go is not in PATH' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'go' }
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 1
        }

        It 'Writes error annotation when go missing' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'go' }
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke Write-CIAnnotation -Times 1 -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*go*not*'
            }
        }

        It 'Returns 1 when golangci-lint not found and install fails' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 1 }
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 1
        }

        It 'Writes error annotation when golangci-lint install fails' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 1 }
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*golangci-lint*'
            }
        }
    }

    Context 'go.mod guard' {
        BeforeEach {
            Remove-Item -Path (Join-Path $script:TestGoDir 'go.mod') -Force
        }

        It 'Returns 0 when go.mod does not exist' {
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 0
        }

        It 'Writes empty results JSON when go.mod missing' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $script:TestOutputPath | Should -Exist
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.packages_tested | Should -Be 0
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Writes step summary when go.mod missing' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'no Go test files' {
        It 'Returns 0 when go test produces no output' {
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 0
        }

        It 'Creates output JSON with zero packages' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.packages_tested | Should -Be 0
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Reports lint passed with no tests' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
        }
    }

    Context 'lint success and test pass' {
        BeforeEach {
            Mock go {
                $global:LASTEXITCODE = 0
                @(
                    '{"Time":"2026-03-24T10:00:00Z","Action":"start","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e"}'
                    '{"Time":"2026-03-24T10:00:00Z","Action":"run","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestVnetExists"}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"pass","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestVnetExists","Elapsed":0.5}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"pass","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Elapsed":1.2}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Returns 0 when all tests pass' {
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 0
        }

        It 'JSON reports correct pass count' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.total_passed | Should -Be 1
            $json.summary.total_failed | Should -Be 0
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Reports lint passed in JSON' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
        }

        It 'Captures Go version in JSON' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.go_version | Should -BeLike '*go1.26*'
        }

        It 'Writes step summary' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'lint failure' {
        BeforeEach {
            Mock golangci-lint {
                $global:LASTEXITCODE = 1
                return 'some lint error'
            } -ParameterFilter { $args[0] -eq 'run' }
        }

        It 'Returns 1 when golangci-lint fails' {
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 1
        }

        It 'Reports lint_passed false in JSON' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeFalse
        }

        It 'Writes error annotation for lint failure' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*golangci-lint*failed*'
            }
        }
    }

    Context 'test failure' {
        BeforeEach {
            Mock go {
                $global:LASTEXITCODE = 1
                @(
                    '{"Time":"2026-03-24T10:00:00Z","Action":"start","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e"}'
                    '{"Time":"2026-03-24T10:00:00Z","Action":"run","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestVnetExists"}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"pass","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestVnetExists","Elapsed":0.5}'
                    '{"Time":"2026-03-24T10:00:00Z","Action":"run","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestSubnetCIDR"}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"fail","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestSubnetCIDR","Elapsed":0.3}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"fail","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Elapsed":1.5}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Returns 1 when any test fails' {
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 1
        }

        It 'Writes error annotation for failed test' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*failed*TestSubnetCIDR*'
            }
        }

        It 'JSON reports correct fail count' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.total_passed | Should -Be 1
            $json.summary.total_failed | Should -Be 1
            $json.summary.overall_passed | Should -BeFalse
        }
    }

    Context 'ChangedFilesOnly' {
        It 'Returns 0 early when no Go files changed' {
            Mock Get-ChangedFilesFromGit { return @() }
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir -ChangedFilesOnly
            $result | Should -Be 0
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.packages_tested | Should -Be 0
        }

        It 'Calls Get-ChangedFilesFromGit with correct extensions' {
            Mock Get-ChangedFilesFromGit { return @() }
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir -ChangedFilesOnly
            Should -Invoke Get-ChangedFilesFromGit -Times 1 -ParameterFilter {
                ($FileExtensions -contains '*.go') -and ($FileExtensions -contains 'go.mod')
            }
        }

        It 'Runs tests when Go files have changed' {
            Mock Get-ChangedFilesFromGit {
                return @('infrastructure/terraform/e2e/main_test.go')
            }
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir -ChangedFilesOnly
            $result | Should -Be 0
            Should -Invoke go -ParameterFilter { $args[0] -eq 'test' }
        }
    }

    Context 'output file creation' {
        It 'Creates output directory if it does not exist' {
            $nestedOutput = Join-Path $TestDrive 'deep/nested/output.json'
            Invoke-GoTestCore -OutputPath $nestedOutput `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Split-Path $nestedOutput -Parent | Should -Exist
        }

        It 'Writes valid JSON to output path' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            { Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json } | Should -Not -Throw
        }
    }

    Context 'multiple packages' {
        BeforeEach {
            Mock go {
                $global:LASTEXITCODE = 1
                @(
                    '{"Time":"2026-03-24T10:00:00Z","Action":"start","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e"}'
                    '{"Time":"2026-03-24T10:00:00Z","Action":"run","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestOne"}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"pass","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Test":"TestOne","Elapsed":0.5}'
                    '{"Time":"2026-03-24T10:00:01Z","Action":"pass","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e","Elapsed":1.0}'
                    '{"Time":"2026-03-24T10:00:02Z","Action":"start","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e/helpers"}'
                    '{"Time":"2026-03-24T10:00:02Z","Action":"run","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e/helpers","Test":"TestTwo"}'
                    '{"Time":"2026-03-24T10:00:03Z","Action":"fail","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e/helpers","Test":"TestTwo","Elapsed":0.8}'
                    '{"Time":"2026-03-24T10:00:03Z","Action":"fail","Package":"github.com/microsoft/physical-ai-toolchain/infrastructure/terraform/e2e/helpers","Elapsed":1.2}'
                )
            } -ParameterFilter { $args[0] -eq 'test' }
        }

        It 'Reports multiple packages in JSON' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.packages_tested | Should -Be 2
        }

        It 'Aggregates pass and fail counts across packages' {
            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.total_passed | Should -Be 1
            $json.summary.total_failed | Should -Be 1
        }

        It 'Returns 1 when any package has failures' {
            $result = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $result | Should -Be 1
        }
    }

    Context 'golangci-lint installation' {
        It 'Calls install script via bash when lint not found' {
            $script:getLintCallCount = 0
            Mock Get-Command {
                $script:getLintCallCount++
                if ($script:getLintCallCount -le 1) { return $null }
                return @{ Source = '/usr/local/bin/golangci-lint' }
            } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 0 }

            Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            Should -Invoke bash -Times 1
        }

        It 'Returns 1 when install fails' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 1 }

            $exitCode = Invoke-GoTestCore -OutputPath $script:TestOutputPath `
                -CoverageOutput $script:TestCoveragePath -GoTestDir $script:TestGoDir
            $exitCode | Should -Be 1
        }
    }
}
