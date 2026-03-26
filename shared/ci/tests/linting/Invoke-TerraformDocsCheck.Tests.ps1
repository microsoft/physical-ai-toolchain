# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '', Justification = 'Mock stub for terraform-docs CLI')]
param()

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-TerraformDocsCheck.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function terraform-docs { }
    function npm { }
}

Describe 'Invoke-TerraformDocsCheckCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/terraform-docs-check-results.json'
        $script:TestTerraformDir = Join-Path $TestDrive 'infrastructure/terraform'
        New-Item -ItemType Directory -Force -Path $script:TestTerraformDir | Out-Null

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Get-Command { return @{ Name = 'terraform-docs' } } -ParameterFilter { $Name -eq 'terraform-docs' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}

        # Default: npm run docs:tf -- --check succeeds (no drift)
        Mock npm {
            $global:LASTEXITCODE = 0
            return 'All documents are up to date'
        }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when terraform-docs is not in PATH' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform-docs' }
            $result = Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Writes error annotation when terraform-docs missing' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform-docs' }
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -Times 1 -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*terraform-docs*not*'
            }
        }
    }

    Context 'clean run (no drift)' {
        It 'Returns 0 when npm run docs:tf passes' {
            $result = Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 0
        }

        It 'Creates output JSON file' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $script:TestOutputPath | Should -Exist
        }

        It 'JSON has correct structure' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json | Should -Not -BeNullOrEmpty
            $json.PSObject.Properties.Name | Should -Contain 'drift_detected'
            $json.PSObject.Properties.Name | Should -Contain 'drifted_files'
            $json.summary | Should -Not -BeNullOrEmpty
        }

        It 'summary.overall_passed is true' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.summary.overall_passed | Should -BeTrue
        }

        It 'Step summary is written' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'drift detected' {
        BeforeEach {
            Mock npm {
                $global:LASTEXITCODE = 1
                return @(
                    'diff --git a/infrastructure/terraform/README.md b/infrastructure/terraform/README.md',
                    'index abc..def 100644',
                    '--- a/infrastructure/terraform/README.md',
                    '+++ b/infrastructure/terraform/README.md'
                )
            }
        }

        It 'Returns 1 when drift detected' {
            $result = Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }

        It 'Captures drifted files in JSON output' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.drifted_files | Should -Contain 'infrastructure/terraform/README.md'
        }

        It 'Writes error annotations for drifted files' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Error' -and $Message -like '*out of date*'
            }
        }

        It 'drift_detected is true in output' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.drift_detected | Should -BeTrue
        }
    }

    Context 'change detection (ChangedFilesOnly)' {
        BeforeEach {
            Mock Get-ChangedFilesFromGit { return @() }
        }

        It 'Returns 0 early when no terraform files changed' {
            $result = Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $result | Should -Be 0
        }

        It 'Does not invoke npm when no files changed' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            Should -Invoke npm -Times 0
        }

        It 'Sets skipped=true in JSON when no files changed' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            $json = Get-Content $script:TestOutputPath -Raw | ConvertFrom-Json
            $json.skipped | Should -BeTrue
        }
    }

    Context 'change detection with terraform-docs config changes' {
        BeforeEach {
            Mock Get-ChangedFilesFromGit {
                if ($FileExtensions -contains '*.yml') {
                    return @('.terraform-docs.yml')
                }
                return @()
            }
        }

        It 'Runs full check when config file changed' {
            Invoke-TerraformDocsCheckCore -OutputPath $script:TestOutputPath `
                -TerraformDir $script:TestTerraformDir -ChangedFilesOnly
            Should -Invoke npm -Times 1
        }
    }
}
