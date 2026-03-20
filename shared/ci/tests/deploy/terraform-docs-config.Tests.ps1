#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
#Requires -Modules powershell-yaml

BeforeAll {
    $script:configPath = Join-Path $PSScriptRoot '../../../../' '.terraform-docs.yml' | Resolve-Path
    $script:config = Get-Content $script:configPath -Raw | ConvertFrom-Yaml
}

Describe '.terraform-docs.yml Configuration' -Tag 'Unit' {
    Context 'File existence and structure' {
        It 'Should exist at repository root' {
            $script:configPath | Should -Exist
        }

        It 'Should be valid YAML' {
            $script:config | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Formatter settings' {
        It 'Should use markdown table formatter' {
            $script:config.formatter | Should -Be 'markdown table'
        }

        It 'Should specify a version constraint' {
            $script:config.version | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Output settings' {
        It 'Should use inject mode' {
            $script:config.output.mode | Should -Be 'inject'
        }

        It 'Should target README.md' {
            $script:config.output.file | Should -Be 'README.md'
        }

        It 'Should include BEGIN_TF_DOCS marker in template' {
            $script:config.output.template | Should -Match 'BEGIN_TF_DOCS'
        }
    }

    Context 'Lint compliance settings' {
        It 'Should disable HTML anchors for MD033 compliance' {
            $script:config.settings.anchor | Should -BeFalse
        }

        It 'Should disable HTML output for MD033 compliance' {
            $script:config.settings.html | Should -BeFalse
        }

        It 'Should enable escape for MD034 compliance' {
            $script:config.settings.escape | Should -BeTrue
        }
    }

    Context 'Section configuration' {
        It 'Should show inputs, outputs, and resources sections' {
            $script:config.sections.show | Should -Contain 'inputs'
            $script:config.sections.show | Should -Contain 'outputs'
            $script:config.sections.show | Should -Contain 'resources'
        }

        It 'Should show exactly 3 sections' {
            $script:config.sections.show | Should -HaveCount 3
        }
    }

    Context 'Sort configuration' {
        It 'Should enable sorting' {
            $script:config.sort.enabled | Should -BeTrue
        }

        It 'Should sort by required' {
            $script:config.sort.by | Should -Be 'required'
        }
    }
}
