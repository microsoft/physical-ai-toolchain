# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

# Stub functions for external tools trigger PSUseApprovedVerbs
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '')]
param()

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-GoLint.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function golangci-lint { }
    function go { }
}

Describe 'Invoke-GoLintCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/go-lint-results.json'
        $script:TestModuleDir = Join-Path $TestDrive 'infrastructure/terraform/e2e'
        New-Item -ItemType Directory -Force -Path $script:TestModuleDir | Out-Null
        New-Item -ItemType File -Force -Path (Join-Path $script:TestModuleDir 'go.mod') | Out-Null

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}

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
            return '/usr/local/go/bin'
        } -ParameterFilter { $args[0] -eq 'env' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Installs golangci-lint when not in PATH' {
            $script:getLintCallCount = 0
            Mock Get-Command {
                $script:getLintCallCount++
                if ($script:getLintCallCount -le 1) { return $null }
                return @{ Source = '/usr/local/bin/golangci-lint' }
            } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 0 }

            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            Should -Invoke bash -Times 1
        }

        It 'Returns 0 when lint passes after install' {
            $script:getLintCallCount = 0
            Mock Get-Command {
                $script:getLintCallCount++
                if ($script:getLintCallCount -le 1) { return $null }
                return @{ Source = '/usr/local/bin/golangci-lint' }
            } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 0 }

            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $result | Should -Be 0
        }

        It 'Returns 1 when golangci-lint install fails' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 1 }
            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $result | Should -Be 1
        }

        It 'Writes error annotation when install fails' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'golangci-lint' }
            Mock bash { $global:LASTEXITCODE = 1 }
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*golangci-lint*'
            }
        }
    }

    Context 'go.mod guard' {
        BeforeEach {
            Remove-Item -Path (Join-Path $script:TestModuleDir 'go.mod') -Force
        }

        It 'Returns 0 when go.mod does not exist' {
            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $result | Should -Be 0
        }

        It 'Writes empty results JSON when go.mod missing' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $script:TestOutputPath | Should -Exist
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
            $json.violation_count | Should -Be 0
        }

        It 'Writes step summary when go.mod missing' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'ChangedFilesOnly' {
        It 'Returns 0 early when no Go files changed' {
            Mock Get-ChangedFilesFromGit { return @() }
            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath `
                -GoModuleDir $script:TestModuleDir -ChangedFilesOnly
            $result | Should -Be 0
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Calls Get-ChangedFilesFromGit with correct extensions' {
            Mock Get-ChangedFilesFromGit { return @() }
            Invoke-GoLintCore -OutputPath $script:TestOutputPath `
                -GoModuleDir $script:TestModuleDir -ChangedFilesOnly
            Should -Invoke Get-ChangedFilesFromGit -Times 1 -ParameterFilter {
                ($FileExtensions -contains '*.go') -and ($FileExtensions -contains 'go.mod')
            }
        }

        It 'Runs lint when Go files have changed' {
            Mock Get-ChangedFilesFromGit {
                return @('infrastructure/terraform/e2e/main.go')
            }
            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath `
                -GoModuleDir $script:TestModuleDir -ChangedFilesOnly
            $result | Should -Be 0
            Should -Invoke golangci-lint -ParameterFilter { $args[0] -eq 'run' }
        }
    }

    Context 'lint success' {
        It 'Returns 0 when lint passes' {
            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $result | Should -Be 0
        }

        It 'JSON reports lint_passed as true' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
            $json.violation_count | Should -Be 0
        }

        It 'JSON includes golangci_lint_version field' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.golangci_lint_version | Should -Not -BeNullOrEmpty
        }

        It 'Writes step summary' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'lint failure' {
        BeforeEach {
            Mock golangci-lint {
                $global:LASTEXITCODE = 1
                return 'main.go:10:5: unused variable (deadcode)'
            } -ParameterFilter { $args[0] -eq 'run' }
        }

        It 'Returns 1 when golangci-lint fails' {
            $result = Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $result | Should -Be 1
        }

        It 'Reports lint_passed false in JSON' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeFalse
        }

        It 'Writes error annotation for lint failure' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*golangci-lint*failed*'
            }
        }
    }

    Context 'output file creation' {
        It 'Creates output directory if it does not exist' {
            $nestedOutput = Join-Path $TestDrive 'deep/nested/output.json'
            Invoke-GoLintCore -OutputPath $nestedOutput -GoModuleDir $script:TestModuleDir
            Split-Path $nestedOutput -Parent | Should -Exist
        }

        It 'Writes valid JSON to output path' {
            Invoke-GoLintCore -OutputPath $script:TestOutputPath -GoModuleDir $script:TestModuleDir
            { Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json } | Should -Not -Throw
        }
    }
}
