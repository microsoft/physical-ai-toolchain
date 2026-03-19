#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
#Requires -Modules powershell-yaml

BeforeAll {
    $configPath = Join-Path $PSScriptRoot '..\..\..\..\' '.terraform-docs.yml' | Resolve-Path
    $config = Get-Content $configPath -Raw | ConvertFrom-Yaml
}

Describe '.terraform-docs.yml Configuration' -Tag 'Unit' {
    Context 'File existence and structure' {
        It 'Should exist at repository root' {
            $configPath | Should -Exist
        }

        It 'Should be valid YAML' {
            $config | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Formatter settings' {
        It 'Should use markdown table formatter' {
            $config.formatter | Should -Be 'markdown table'
        }

        It 'Should specify a version constraint' {
            $config.version | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Output settings' {
        It 'Should use inject mode' {
            $config.output.mode | Should -Be 'inject'
        }

        It 'Should target README.md' {
            $config.output.file | Should -Be 'README.md'
        }

        It 'Should include BEGIN_TF_DOCS marker in template' {
            $config.output.template | Should -Match 'BEGIN_TF_DOCS'
        }
    }

    Context 'Lint compliance settings' {
        It 'Should disable HTML anchors for MD033 compliance' {
            $config.settings.anchor | Should -BeFalse
        }

        It 'Should disable HTML output for MD033 compliance' {
            $config.settings.html | Should -BeFalse
        }

        It 'Should enable escape for MD034 compliance' {
            $config.settings.escape | Should -BeTrue
        }
    }

    Context 'Section configuration' {
        It 'Should show inputs, outputs, and resources sections' {
            $config.sections.show | Should -Contain 'inputs'
            $config.sections.show | Should -Contain 'outputs'
            $config.sections.show | Should -Contain 'resources'
        }

        It 'Should show exactly 3 sections' {
            $config.sections.show | Should -HaveCount 3
        }
    }

    Context 'Sort configuration' {
        It 'Should enable sorting' {
            $config.sort.enabled | Should -BeTrue
        }

        It 'Should sort by required' {
            $config.sort.by | Should -Be 'required'
        }
    }
}
