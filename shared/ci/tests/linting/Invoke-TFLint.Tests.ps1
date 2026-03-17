# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-TFLint.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function tflint { }
}

Describe 'Invoke-TFLintCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll  { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestOutputPath = Join-Path $TestDrive 'logs/tflint-results.json'
        $script:TestTerraformDir = Join-Path $TestDrive 'infrastructure/terraform'
        $script:TestConfigPath = Join-Path $TestDrive '.tflint.hcl'

        New-Item -ItemType Directory -Force -Path $script:TestTerraformDir | Out-Null
        '{}' | Out-File $script:TestConfigPath

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock tflint { return '{"issues":[],"errors":[]}' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when tflint is not installed' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'tflint' }
            $result = Invoke-TFLintCore -ConfigPath $script:TestConfigPath `
                -OutputPath $script:TestOutputPath -TerraformDir $script:TestTerraformDir
            $result | Should -Be 1
        }
    }

    Context 'clean run' {
        It 'Returns 0 when no issues found' {
            Mock tflint { $global:LASTEXITCODE = 0; return '{"issues":[],"errors":[]}' }
            $result = Invoke-TFLintCore -ConfigPath $script:TestConfigPath `
                -OutputPath $script:TestOutputPath -TerraformDir $script:TestTerraformDir
            $result | Should -Be 0
        }
    }

    Context 'violations found' {
        It 'Returns non-zero when violations found' {
            Mock tflint { $global:LASTEXITCODE = 2; return '{"issues":[{"rule":{"name":"terraform_naming_convention","severity":"warning","link":""},"message":"test violation","range":{"filename":"main.tf","start":{"line":1,"column":1},"end":{"line":1,"column":10}},"callers":[]}],"errors":[]}' }
            $result = Invoke-TFLintCore -ConfigPath $script:TestConfigPath `
                -OutputPath $script:TestOutputPath -TerraformDir $script:TestTerraformDir
            $result | Should -Not -Be 0
        }
    }

    Context 'output file creation' {
        It 'Creates output file' {
            Mock tflint { $global:LASTEXITCODE = 0; return '{"issues":[],"errors":[]}' }
            Invoke-TFLintCore -ConfigPath $script:TestConfigPath `
                -OutputPath $script:TestOutputPath -TerraformDir $script:TestTerraformDir
            $script:TestOutputPath | Should -Exist
        }
    }
}
