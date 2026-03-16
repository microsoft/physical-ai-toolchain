#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-YamlLint.ps1
    $ErrorActionPreference = 'Continue'

    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force

    # Shim for mocking actionlint (may not be installed in test environment)
    function actionlint { }
}

Describe 'Invoke-YamlLintCore' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:WorkflowDir = Join-Path $TestDrive '.github/workflows'
        New-Item -ItemType Directory -Path $script:WorkflowDir -Force | Out-Null
        $script:TestOutputPath = Join-Path $TestDrive 'output/yaml-lint-results.json'

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock actionlint { return '' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
        Remove-Item env:YAML_LINT_FAILED -ErrorAction SilentlyContinue
    }

    Context 'actionlint availability' {
        It 'Returns 1 when actionlint is not installed' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'actionlint' }
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath 2>$null
            $result | Should -Be 1
        }
    }

    Context 'No workflow files to lint' {
        It 'Returns 0 when workflow directory is empty' {
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Returns 0 when workflow directory does not exist' {
            Remove-Item (Join-Path $TestDrive '.github') -Recurse -Force
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Sets CI output to 0 issues' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'issues=0'
        }

        It 'Writes no-files step summary' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            Get-Content $env:GITHUB_STEP_SUMMARY -Raw | Should -Match 'No workflow files to lint'
        }
    }

    Context 'All files mode' {
        BeforeEach {
            'name: CI' | Set-Content (Join-Path $script:WorkflowDir 'ci.yml')
            'name: Deploy' | Set-Content (Join-Path $script:WorkflowDir 'deploy.yaml')
        }

        It 'Returns 0 when no issues found' {
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Discovers yml and yaml files' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $content = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $content.totalFiles | Should -Be 2
        }

        It 'Creates JSON output file' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            Test-Path $script:TestOutputPath | Should -BeTrue
        }

        It 'Writes results step summary' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $summary = Get-Content $env:GITHUB_STEP_SUMMARY -Raw
            $summary | Should -Match 'YAML Lint Results'
            $summary | Should -Match 'Workflow Files'
        }
    }

    Context 'Changed files only mode' {
        It 'Filters to workflow paths' {
            Mock Get-ChangedFilesFromGit {
                return @(
                    '.github/workflows/ci.yml',
                    '.github/workflows/deploy.yml',
                    'docs/config.yaml'
                )
            }

            Invoke-YamlLintCore -ChangedFilesOnly -OutputPath $script:TestOutputPath
            $content = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $content.totalFiles | Should -Be 2
        }

        It 'Returns 0 when no workflow files changed' {
            Mock Get-ChangedFilesFromGit { return @('docs/README.yml') }
            $result = Invoke-YamlLintCore -ChangedFilesOnly -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Returns 0 when no files changed at all' {
            Mock Get-ChangedFilesFromGit { return @() }
            $result = Invoke-YamlLintCore -ChangedFilesOnly -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }
    }

    Context 'Error and warning classification' {
        BeforeEach {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
        }

        It 'Returns 1 when errors found' {
            Mock actionlint {
                return '{"filepath":"test.yml","line":5,"column":3,"kind":"error","message":"unexpected token"}'
            }
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 1
        }

        It 'Returns 0 when only warnings found' {
            Mock actionlint {
                return '{"filepath":"test.yml","line":5,"column":3,"kind":"warning","message":"style issue"}'
            }
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Writes YAML_LINT_FAILED to CI env file on errors' {
            Mock actionlint {
                return '{"filepath":"test.yml","line":5,"column":3,"kind":"error","message":"err"}'
            }
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $envContent = Get-Content $env:GITHUB_ENV -Raw
            $envContent | Should -Match 'YAML_LINT_FAILED'
            $envContent | Should -Match 'true'
        }

        It 'Does not write YAML_LINT_FAILED when no errors' {
            Mock actionlint {
                return '{"filepath":"test.yml","line":5,"column":3,"kind":"warning","message":"warn"}'
            }
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $envContent = Get-Content $env:GITHUB_ENV -Raw -ErrorAction SilentlyContinue
            if ($envContent) {
                $envContent | Should -Not -Match 'YAML_LINT_FAILED'
            }
        }

        It 'Counts errors and warnings separately' {
            $script:mixedJson = @(
                @{ filepath = 'a.yml'; line = 1; column = 1; kind = 'error'; message = 'e1' }
                @{ filepath = 'b.yml'; line = 2; column = 1; kind = 'warning'; message = 'w1' }
                @{ filepath = 'c.yml'; line = 3; column = 1; kind = 'error'; message = 'e2' }
            ) | ConvertTo-Json -Compress
            Mock actionlint { return $script:mixedJson }

            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $content = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $content.errorCount | Should -Be 2
            $content.warningCount | Should -Be 1
        }
    }

    Context 'JSON export structure' {
        BeforeEach {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
            Mock actionlint {
                return '{"filepath":"test.yml","line":5,"column":3,"kind":"error","message":"err"}'
            }
        }

        It 'Contains expected metadata fields' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $content = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $content.PSObject.Properties.Name | Should -Contain 'timestamp'
            $content.PSObject.Properties.Name | Should -Contain 'totalFiles'
            $content.PSObject.Properties.Name | Should -Contain 'errorCount'
            $content.PSObject.Properties.Name | Should -Contain 'warningCount'
            $content.PSObject.Properties.Name | Should -Contain 'issues'
        }

        It 'Creates output directory if needed' {
            $nestedPath = Join-Path $TestDrive 'deep/nested/results.json'
            Invoke-YamlLintCore -OutputPath $nestedPath
            Test-Path $nestedPath | Should -BeTrue
        }

        It 'Records correct file count in export' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $content = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $content.totalFiles | Should -BeGreaterThan 0
        }
    }

    Context 'Invalid actionlint output' {
        BeforeEach {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
        }

        It 'Returns 0 for invalid JSON' {
            Mock actionlint { return 'not valid json {{{' }
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath 3>$null
            $result | Should -Be 0
        }

        It 'Emits warning for invalid JSON' {
            Mock actionlint { return 'not valid json' }
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath -WarningVariable warnings 3>$null
            $warnings | Should -Not -BeNullOrEmpty
        }

        It 'Returns 0 for empty output' {
            Mock actionlint { return '' }
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }

        It 'Returns 0 for null output' {
            Mock actionlint { return $null }
            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Be 0
        }
    }

    Context 'CI output values' {
        BeforeEach {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
        }

        It 'Sets issues count' {
            Mock actionlint {
                return '{"filepath":"t.yml","line":1,"column":1,"kind":"error","message":"e"}'
            }
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'issues=1'
        }

        It 'Sets errors count' {
            Mock actionlint {
                return '{"filepath":"t.yml","line":1,"column":1,"kind":"error","message":"e"}'
            }
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'errors=1'
        }

        It 'Sets warnings count' {
            Mock actionlint {
                return '{"filepath":"t.yml","line":1,"column":1,"kind":"warning","message":"w"}'
            }
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            Get-Content $env:GITHUB_OUTPUT -Raw | Should -Match 'warnings=1'
        }
    }

    Context 'Default output path' {
        It 'Writes to logs/yaml-lint-results.json when OutputPath is empty' {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
            Invoke-YamlLintCore
            $defaultPath = Join-Path $TestDrive 'logs/yaml-lint-results.json'
            Test-Path $defaultPath | Should -BeTrue
        }
    }

    Context 'File path relativization' {
        BeforeEach {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
        }

        It 'Strips repo root from absolute paths in annotations' {
            $absPath = Join-Path $TestDrive '.github/workflows/test.yml'
            $script:relJson = @{ filepath = $absPath; line = 5; column = 3; kind = 'error'; message = 'err' } | ConvertTo-Json -Compress
            Mock actionlint { return $script:relJson }
            Mock Write-CIAnnotation { }

            Invoke-YamlLintCore -OutputPath $script:TestOutputPath

            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $File -match '^\.github[\\/]workflows[\\/]test\.yml$'
            }
        }
    }

    #region Parameter validation
    Context 'BaseBranch parameter passthrough' {
        It 'passes BaseBranch value to Get-ChangedFilesFromGit' {
            $customBranch = 'origin/develop'
            Mock -ModuleName LintingHelpers -CommandName git -MockWith { '' }

            Invoke-YamlLintCore -ChangedFilesOnly -BaseBranch $customBranch

            Should -Invoke -CommandName git -ModuleName LintingHelpers `
                -ParameterFilter { $args -contains $customBranch } -Times 1
        }

        It 'uses default BaseBranch when not specified' {
            Mock -ModuleName LintingHelpers -CommandName git -MockWith { '' }

            Invoke-YamlLintCore -ChangedFilesOnly

            Should -Invoke -CommandName git -ModuleName LintingHelpers `
                -ParameterFilter { $args -contains 'origin/main' } -Times 1
        }
    }

    Context 'repoRoot fallback when git rev-parse fails' {
        It 'falls back to script directory when git rev-parse fails' {
            Mock git -ParameterFilter { $args[0] -eq 'rev-parse' -and $args[1] -eq '--show-toplevel' } `
                -MockWith { $null }

            { Invoke-YamlLintCore -OutputPath $script:TestOutputPath } | Should -Not -Throw
        }
    }
    #endregion

    #region OutputPath behavior
    Context 'Default OutputPath from repoRoot' {
        It 'derives OutputPath from repository root when not specified' {
            Invoke-YamlLintCore

            $outputContent = Get-Content $env:GITHUB_OUTPUT -Raw
            $outputContent | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Parent directory creation for OutputPath' {
        It 'creates parent directories when they do not exist' {
            'name: Test' | Set-Content (Join-Path $script:WorkflowDir 'test.yml')
            $nestedPath = Join-Path $TestDrive 'nested/deep/output.json'

            Invoke-YamlLintCore -OutputPath $nestedPath

            Test-Path (Split-Path $nestedPath -Parent) | Should -BeTrue
        }
    }
    #endregion

    #region Exit code propagation
    Context 'YAML_LINT_FAILED environment variable on errors' {
        It 'sets YAML_LINT_FAILED=true when actionlint returns errors' {
            Mock actionlint -MockWith {
                '[{"filepath":"test.yml","line":1,"column":1,"message":"error","kind":"syntax"}]'
            }

            'name: CI' | Set-Content (Join-Path $script:WorkflowDir 'ci.yml')

            Invoke-YamlLintCore -OutputPath $script:TestOutputPath

            $envContent = Get-Content $env:GITHUB_ENV -Raw
            $envContent | Should -Match 'YAML_LINT_FAILED'
            $envContent | Should -Match 'true'
        }
    }

    Context 'Exit code verification' {
        It 'returns zero exit code on clean run' {
            Mock actionlint -MockWith { '[]' }

            'name: CI' | Set-Content (Join-Path $script:WorkflowDir 'ci.yml')

            $result = Invoke-YamlLintCore -OutputPath $script:TestOutputPath
            $result | Should -Not -BeNullOrEmpty
            $outputContent = Get-Content $env:GITHUB_OUTPUT -Raw
            $outputContent | Should -Match 'errors=0'
        }

        It 'reports non-zero errors when issues found' {
            Mock actionlint -MockWith {
                '[{"filepath":"test.yml","line":1,"column":1,"message":"bad","kind":"syntax"}]'
            }

            'name: CI' | Set-Content (Join-Path $script:WorkflowDir 'ci.yml')

            Invoke-YamlLintCore -OutputPath $script:TestOutputPath

            $outputContent = Get-Content $env:GITHUB_OUTPUT -Raw
            $outputContent | Should -Not -Match 'errors=0'
        }
    }
    #endregion

    #region Step summary and CI output
    Context 'Step summary markdown table content' {
        It 'writes step summary with issue counts' {
            Mock actionlint -MockWith {
                '[{"filepath":"ci.yml","line":1,"column":1,"message":"warn","kind":"expression"}]'
            }

            'name: CI' | Set-Content (Join-Path $script:WorkflowDir 'ci.yml')

            Invoke-YamlLintCore -OutputPath $script:TestOutputPath

            $summaryContent = Get-Content $env:GITHUB_STEP_SUMMARY -Raw
            $summaryContent | Should -Match '\|'
            $summaryContent | Should -Match 'Errors'
        }
    }

    Context 'CI outputs on zero-issue no-files path' {
        It 'writes zero-count outputs when no workflow files exist' {
            Invoke-YamlLintCore -OutputPath $script:TestOutputPath

            $outputContent = Get-Content $env:GITHUB_OUTPUT -Raw
            $outputContent | Should -Match 'issues=0'
        }
    }
    #endregion
}

Describe 'Dot-sourced execution protection' -Tag 'Unit' {
    It 'Does not execute main block when dot-sourced' {
        $testScript = Join-Path $PSScriptRoot '../../linting/Invoke-YamlLint.ps1'
        $tempOutputPath = Join-Path $TestDrive 'dot-source-test.json'

        pwsh -Command ". '$testScript' -OutputPath '$tempOutputPath'; [System.IO.File]::Exists('$tempOutputPath')" 2>&1 | Out-Null

        Test-Path $tempOutputPath | Should -BeFalse
    }
}
