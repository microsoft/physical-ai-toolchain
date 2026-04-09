# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '', Justification = 'Mock stub for terraform-docs CLI')]
param()

BeforeAll {
    . $PSScriptRoot/../../Update-TerraformDocs.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function terraform-docs { }
}

Describe 'Update-TerraformDocsCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $script:TestTerraformDir = Join-Path $TestDrive 'infrastructure/terraform'
        $script:TestConfigPath = Join-Path $TestDrive '.terraform-docs.yml'

        # Create terraform dir with .tf files
        New-Item -ItemType Directory -Force -Path $script:TestTerraformDir | Out-Null
        'resource "azurerm_resource_group" "rg" {}' | Out-File (Join-Path $script:TestTerraformDir 'main.tf')

        # Create config file
        'formatter: "markdown table"' | Out-File $script:TestConfigPath

        # Create subdirectory with .tf files
        $vpnDir = Join-Path $script:TestTerraformDir 'vpn'
        New-Item -ItemType Directory -Force -Path $vpnDir | Out-Null
        'resource "azurerm_virtual_network_gateway" "gw" {}' | Out-File (Join-Path $vpnDir 'main.tf')

        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Get-Command { return @{ Name = 'terraform-docs' } } -ParameterFilter { $Name -eq 'terraform-docs' }

        # Default: terraform-docs succeeds silently
        Mock terraform-docs { $global:LASTEXITCODE = 0 }
        # Default: git diff shows no changes (clean)
        Mock git { $global:LASTEXITCODE = 0; return '' } -ParameterFilter { $args[0] -eq 'diff' }
        Mock git { } -ParameterFilter { $args[0] -eq 'checkout' }
    }

    AfterEach {
        Restore-CIEnvironment
        Remove-MockCIFiles -MockFiles $script:MockFiles
    }

    Context 'tool availability' {
        It 'Returns 1 when terraform-docs is not in PATH' {
            Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'terraform-docs' }
            $result = Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            $result | Should -Be 1
        }

        It 'Returns 1 when config file does not exist' {
            $result = Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath (Join-Path $TestDrive 'nonexistent.yml')
            $result | Should -Be 1
        }
    }

    Context 'directory discovery' {
        It 'Discovers root terraform directory' {
            Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            Should -Invoke terraform-docs -ParameterFilter {
                $args -contains $script:TestTerraformDir
            }
        }

        It 'Discovers subdirectories containing .tf files' {
            Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            $vpnDir = Join-Path $script:TestTerraformDir 'vpn'
            Should -Invoke terraform-docs -ParameterFilter {
                $args -contains $vpnDir
            }
        }

        It 'Skips subdirectories without .tf files' {
            $emptyDir = Join-Path $script:TestTerraformDir 'empty-module'
            New-Item -ItemType Directory -Force -Path $emptyDir | Out-Null
            'not a terraform file' | Out-File (Join-Path $emptyDir 'notes.txt')

            Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            Should -Invoke terraform-docs -Times 0 -ParameterFilter {
                $args -contains $emptyDir
            }
        }
    }

    Context 'generate mode (no -Check)' {
        It 'Returns 0 on successful generation' {
            $result = Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            $result | Should -Be 0
        }

        It 'Invokes terraform-docs for each discovered directory' {
            Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            # Root + vpn subdirectory = 2 invocations
            Should -Invoke terraform-docs -Times 2
        }

        It 'Passes correct config path to terraform-docs' {
            Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            Should -Invoke terraform-docs -ParameterFilter {
                $args -contains $script:TestConfigPath
            }
        }

        It 'Returns 1 when terraform-docs fails' {
            Mock terraform-docs { $global:LASTEXITCODE = 1 }
            $result = Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath
            $result | Should -Be 1
        }
    }

    Context 'check mode (-Check)' {
        It 'Returns 0 when no drift detected' {
            $result = Update-TerraformDocsCore -Check `
                -TerraformDir $script:TestTerraformDir -ConfigPath $script:TestConfigPath
            $result | Should -Be 0
        }

        It 'Returns 1 when drift detected' {
            Mock git {
                $global:LASTEXITCODE = 0
                return @(
                    'diff --git a/infrastructure/terraform/README.md b/infrastructure/terraform/README.md',
                    '--- a/infrastructure/terraform/README.md',
                    '+++ b/infrastructure/terraform/README.md',
                    '+| Name | Description |'
                )
            } -ParameterFilter { $args[0] -eq 'diff' }

            $result = @(Update-TerraformDocsCore -Check `
                -TerraformDir $script:TestTerraformDir -ConfigPath $script:TestConfigPath)
            $result[-1] | Should -Be 1
        }

        It 'Outputs diff for CI wrapper to parse' {
            Mock git {
                $global:LASTEXITCODE = 0
                return @(
                    'diff --git a/infrastructure/terraform/README.md b/infrastructure/terraform/README.md',
                    '--- a/infrastructure/terraform/README.md',
                    '+++ b/infrastructure/terraform/README.md'
                )
            } -ParameterFilter { $args[0] -eq 'diff' }

            $output = Update-TerraformDocsCore -Check `
                -TerraformDir $script:TestTerraformDir -ConfigPath $script:TestConfigPath
            $strings = $output | ForEach-Object { "$_" }
            $strings | Should -Contain 'diff --git a/infrastructure/terraform/README.md b/infrastructure/terraform/README.md'
        }

        It 'Restores original files after check' {
            Mock git {
                $global:LASTEXITCODE = 0
                return @('diff --git a/infrastructure/terraform/README.md b/infrastructure/terraform/README.md')
            } -ParameterFilter { $args[0] -eq 'diff' }

            Update-TerraformDocsCore -Check `
                -TerraformDir $script:TestTerraformDir -ConfigPath $script:TestConfigPath
            Should -Invoke git -ParameterFilter { $args[0] -eq 'checkout' }
        }
    }

    Context 'npm passthrough args (--check)' {
        It 'Activates check mode when --check in PassthroughArgs' {
            Update-TerraformDocsCore -TerraformDir $script:TestTerraformDir `
                -ConfigPath $script:TestConfigPath -PassthroughArgs @('--check')
            # Check mode invokes git diff
            Should -Invoke git -ParameterFilter { $args[0] -eq 'diff' }
        }
    }
}
