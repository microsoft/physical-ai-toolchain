# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

# Stub function for external tool triggers PSUseApprovedVerbs
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '')]
param()

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-ShellCheck.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function shellcheck { }
}

Describe 'Invoke-ShellCheckCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/shellcheck-results.json'

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}

        Mock shellcheck {
            return 'ShellCheck - shell script analysis tool'
        } -ParameterFilter { $args[0] -eq '--version' }

        Mock Get-Command {
            return @{ Source = '/usr/bin/shellcheck' }
        } -ParameterFilter { $Name -eq 'shellcheck' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when shellcheck is not on PATH' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'shellcheck' }
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'test.sh')

            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 1
        }

        It 'Writes error annotation when shellcheck not found' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'shellcheck' }
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'test.sh')

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*shellcheck*not found*'
            }
        }
    }

    Context 'no files found' {
        It 'Returns 0 when no .sh files exist' {
            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Writes empty results JSON when no .sh files exist' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $script:TestOutputPath | Should -Exist
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
            $json.error_count | Should -Be 0
            $json.warning_count | Should -Be 0
        }

        It 'Writes step summary when no .sh files found' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'ChangedFilesOnly' {
        It 'Returns 0 early when no shell files changed' {
            Mock Get-ChangedFilesFromGit { return @() }
            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath -ChangedFilesOnly
            $result | Should -Be 0
        }

        It 'Writes empty results when no shell files changed' {
            Mock Get-ChangedFilesFromGit { return @() }
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
        }

        It 'Calls Get-ChangedFilesFromGit with *.sh extension' {
            Mock Get-ChangedFilesFromGit { return @() }
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath -ChangedFilesOnly
            Should -Invoke Get-ChangedFilesFromGit -Times 1 -ParameterFilter {
                $FileExtensions -contains '*.sh'
            }
        }

        It 'Lints only changed files when ChangedFilesOnly is set' {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'changed.sh')
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'unchanged.sh')

            Mock Get-ChangedFilesFromGit { return @('scripts/changed.sh') }
            Mock shellcheck { return $null } -ParameterFilter { $args[0] -eq '--format=json' }

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath -ChangedFilesOnly
            Should -Invoke shellcheck -Times 1 -ParameterFilter { $args[0] -eq '--format=json' }
        }
    }

    Context 'lint success' {
        BeforeEach {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'clean.sh')

            Mock shellcheck { return $null } -ParameterFilter { $args[0] -eq '--format=json' }
        }

        It 'Returns 0 when no issues found' {
            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Reports lint_passed as true in JSON' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeTrue
        }

        It 'Records files_checked count' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.files_checked | Should -Be 1
        }

        It 'Writes step summary on success' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'error classification' {
        BeforeEach {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'bad.sh')
        }

        It 'Returns 1 when errors found' {
            Mock shellcheck {
                return '[{"file":"bad.sh","line":2,"column":1,"level":"error","code":2086,"message":"Double quote to prevent globbing"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }

            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 1
        }

        It 'Counts errors correctly in JSON' {
            Mock shellcheck {
                return '[{"file":"bad.sh","line":2,"column":1,"level":"error","code":2086,"message":"err1"},{"file":"bad.sh","line":3,"column":1,"level":"error","code":2087,"message":"err2"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.error_count | Should -Be 2
            $json.warning_count | Should -Be 0
        }

        It 'Writes error annotations for each issue' {
            Mock shellcheck {
                return '[{"file":"bad.sh","line":2,"column":1,"level":"error","code":2086,"message":"Double quote"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            Should -Invoke Write-CIAnnotation -ParameterFilter { $Level -eq 'Error' }
        }
    }

    Context 'warning classification (strict mode)' {
        BeforeEach {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'warn.sh')
        }

        It 'Returns 1 when only warnings found' {
            Mock shellcheck {
                return '[{"file":"warn.sh","line":2,"column":1,"level":"warning","code":2034,"message":"var unused"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }

            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 1
        }

        It 'Reports lint_passed as false when warnings present' {
            Mock shellcheck {
                return '[{"file":"warn.sh","line":2,"column":1,"level":"warning","code":2034,"message":"var unused"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.lint_passed | Should -BeFalse
        }

        It 'Counts warnings correctly in JSON' {
            Mock shellcheck {
                return '[{"file":"warn.sh","line":2,"column":1,"level":"warning","code":2034,"message":"w1"},{"file":"warn.sh","line":3,"column":1,"level":"warning","code":2035,"message":"w2"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.warning_count | Should -Be 2
            $json.error_count | Should -Be 0
        }
    }

    Context 'mixed errors and warnings' {
        BeforeEach {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'mixed.sh')

            Mock shellcheck {
                return '[{"file":"mixed.sh","line":2,"column":1,"level":"error","code":2086,"message":"err"},{"file":"mixed.sh","line":3,"column":1,"level":"warning","code":2034,"message":"warn"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }
        }

        It 'Returns 1 with mixed issues' {
            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 1
        }

        It 'Counts errors and warnings separately' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.error_count | Should -Be 1
            $json.warning_count | Should -Be 1
        }
    }

    Context 'JSON export structure' {
        BeforeEach {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'test.sh')

            Mock shellcheck {
                return '[{"file":"test.sh","line":2,"column":1,"level":"error","code":2086,"message":"err"}]'
            } -ParameterFilter { $args[0] -eq '--format=json' }
        }

        It 'Contains expected metadata fields' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.PSObject.Properties.Name | Should -Contain 'timestamp'
            $json.PSObject.Properties.Name | Should -Contain 'shellcheck_version'
            $json.PSObject.Properties.Name | Should -Contain 'lint_passed'
            $json.PSObject.Properties.Name | Should -Contain 'error_count'
            $json.PSObject.Properties.Name | Should -Contain 'warning_count'
            $json.PSObject.Properties.Name | Should -Contain 'files_checked'
            $json.PSObject.Properties.Name | Should -Contain 'issues'
        }

        It 'Includes issue details in issues array' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.issues.Count | Should -Be 1
            $json.issues[0].code | Should -Be 2086
            $json.issues[0].level | Should -Be 'error'
        }

        It 'Includes summary.overall_passed field' {
            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.overall_passed | Should -BeFalse
        }
    }

    Context 'directory exclusion' {
        BeforeEach {
            # Create .sh files in excluded directories
            foreach ($dir in @('.venv', 'external', 'node_modules', 'docs/docusaurus')) {
                $excludedDir = Join-Path $TestDrive $dir
                New-Item -ItemType Directory -Force -Path $excludedDir | Out-Null
                '#!/bin/bash' | Set-Content (Join-Path $excludedDir 'excluded.sh')
            }
        }

        It 'Returns 0 when all .sh files are in excluded directories' {
            Mock shellcheck { return $null } -ParameterFilter { $args[0] -eq '--format=json' }
            $result = Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }
    }

    Context 'output file creation' {
        It 'Creates output directory if it does not exist' {
            $nestedOutput = Join-Path $TestDrive 'deep/nested/output.json'
            Invoke-ShellCheckCore -OutputPath $nestedOutput
            Split-Path $nestedOutput -Parent | Should -Exist
        }

        It 'Writes valid JSON to output path' {
            $shDir = Join-Path $TestDrive 'scripts'
            New-Item -ItemType Directory -Force -Path $shDir | Out-Null
            '#!/bin/bash' | Set-Content (Join-Path $shDir 'test.sh')
            Mock shellcheck { return $null } -ParameterFilter { $args[0] -eq '--format=json' }

            Invoke-ShellCheckCore -OutputPath $script:TestOutputPath
            { Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json } | Should -Not -Throw
        }

        It 'Uses default output path when not specified' {
            Invoke-ShellCheckCore
            $defaultPath = Join-Path $TestDrive 'logs/shellcheck-results.json'
            $defaultPath | Should -Exist
        }
    }
}
