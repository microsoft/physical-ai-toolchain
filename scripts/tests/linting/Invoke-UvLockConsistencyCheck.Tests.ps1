# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

# Stub function for external tool triggers PSUseApprovedVerbs
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseApprovedVerbs', '')]
param()

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-UvLockConsistencyCheck.ps1
    $ErrorActionPreference = 'Continue'
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    function uv { }
}

Describe 'Get-UvLockProject' -Tag 'Unit' {
    It 'Discovers directories containing uv.lock as repo-relative paths' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'training/rl') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'evaluation') | Out-Null
        'lock' | Set-Content (Join-Path $TestDrive 'uv.lock')
        'lock' | Set-Content (Join-Path $TestDrive 'training/rl/uv.lock')
        'lock' | Set-Content (Join-Path $TestDrive 'evaluation/uv.lock')

        $projects = Get-UvLockProject -RepoRoot $TestDrive

        $projects | Should -Contain '.'
        $projects | Should -Contain 'training/rl'
        $projects | Should -Contain 'evaluation'
        $projects.Count | Should -Be 3
    }

    It 'Excludes vendored and tooling directories' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'external/IsaacLab') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive '.venv') | Out-Null
        'lock' | Set-Content (Join-Path $TestDrive 'external/IsaacLab/uv.lock')
        'lock' | Set-Content (Join-Path $TestDrive '.venv/uv.lock')

        $projects = Get-UvLockProject -RepoRoot $TestDrive

        $projects | Should -Not -Contain 'external/IsaacLab'
        $projects | Should -Not -Contain '.venv'
    }

    It 'Returns an empty array when no uv.lock exists' {
        $emptyRoot = Join-Path $TestDrive 'empty'
        New-Item -ItemType Directory -Force -Path $emptyRoot | Out-Null

        $projects = @(Get-UvLockProject -RepoRoot $emptyRoot)

        $projects.Count | Should -Be 0
    }
}

Describe 'Invoke-UvLockCheck' -Tag 'Unit' {
    It 'Returns the exit code and trimmed output from uv' {
        Mock uv {
            $global:LASTEXITCODE = 0
            "  lock is up to date  `n"
        }

        $result = Invoke-UvLockCheck -ProjectDirectory $TestDrive

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Be 'lock is up to date'
    }

    It 'Surfaces a non-zero exit code from uv' {
        Mock uv {
            $global:LASTEXITCODE = 2
            'lock is out of date'
        }

        $result = Invoke-UvLockCheck -ProjectDirectory $TestDrive

        $result.ExitCode | Should -Be 2
        $result.Output | Should -Be 'lock is out of date'
    }
}

Describe 'Test-UvLockProject' -Tag 'Unit' {
    It 'Reports Passed when uv lock --check exits zero' {
        Mock Invoke-UvLockCheck {
            [pscustomobject]@{ ExitCode = 0; Output = '' }
        }

        $result = Test-UvLockProject -Project 'training/rl' -RepoRoot $TestDrive

        $result.Project | Should -Be 'training/rl'
        $result.Passed | Should -BeTrue
    }

    It 'Reports drift when uv lock --check exits non-zero' {
        Mock Invoke-UvLockCheck {
            [pscustomobject]@{ ExitCode = 1; Output = 'The lockfile is out of date' }
        }

        $result = Test-UvLockProject -Project 'evaluation' -RepoRoot $TestDrive

        $result.Passed | Should -BeFalse
        $result.Detail | Should -Be 'The lockfile is out of date'
    }

    It 'Resolves the root project to the repository root' {
        Mock Invoke-UvLockCheck {
            [pscustomobject]@{ ExitCode = 0; Output = '' }
        } -ParameterFilter { $ProjectDirectory -eq $TestDrive }

        $result = Test-UvLockProject -Project '.' -RepoRoot $TestDrive

        $result.Passed | Should -BeTrue
        Should -Invoke Invoke-UvLockCheck -ParameterFilter { $ProjectDirectory -eq $TestDrive }
    }
}

Describe 'New-UvLockReport' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:OutputPath = Join-Path $TestDrive 'logs/uv-lock-consistency-results.json'
        Mock Write-CIStepSummary {}
    }

    It 'Writes JSON marking the run passed when no drift' {
        $results = @([pscustomobject]@{ Project = 'training/rl'; Passed = $true; Detail = '' })

        $report = New-UvLockReport -Results $results -OutputPath $script:OutputPath

        $report.DriftCount | Should -Be 0
        $script:OutputPath | Should -Exist
        $json = Get-Content $script:OutputPath -Raw | ConvertFrom-Json
        $json.check_passed | Should -BeTrue
        $json.projects_count | Should -Be 1
        $json.drift_count | Should -Be 0
    }

    It 'Records drifted projects in the JSON and summary' {
        $results = @(
            [pscustomobject]@{ Project = 'training/rl'; Passed = $true; Detail = '' },
            [pscustomobject]@{ Project = 'evaluation'; Passed = $false; Detail = 'out of date' }
        )

        $report = New-UvLockReport -Results $results -OutputPath $script:OutputPath

        $report.DriftCount | Should -Be 1
        $json = Get-Content $script:OutputPath -Raw | ConvertFrom-Json
        $json.check_passed | Should -BeFalse
        $json.drift_count | Should -Be 1
        $report.MarkdownPath | Should -Exist
        (Get-Content $report.MarkdownPath -Raw) | Should -Match 'evaluation'
    }

    It 'Writes a step summary' {
        New-UvLockReport -Results @() -OutputPath $script:OutputPath | Out-Null
        Should -Invoke Write-CIStepSummary -Times 1
    }
}

Describe 'Select-ChangedProject' -Tag 'Unit' {
    It 'Returns an empty array when no lock or manifest changed' {
        Mock Get-ChangedFilesFromGit { @() }

        $result = @(Select-ChangedProject -Project @('training/rl', 'evaluation'))

        $result.Count | Should -Be 0
    }

    It 'Keeps only projects whose uv.lock or pyproject.toml changed' {
        Mock Get-ChangedFilesFromGit { @('training/rl/uv.lock', 'data-pipeline/pyproject.toml', 'docs/README.md') }

        $result = @(Select-ChangedProject -Project @('training/rl', 'evaluation', 'data-pipeline'))

        $result | Should -Contain 'training/rl'
        $result | Should -Contain 'data-pipeline'
        $result | Should -Not -Contain 'evaluation'
    }

    It 'Maps a root-level lock change to the root project' {
        Mock Get-ChangedFilesFromGit { @('uv.lock') }

        $result = @(Select-ChangedProject -Project @('.', 'training/rl'))

        $result | Should -Contain '.'
        $result | Should -Not -Contain 'training/rl'
    }

    It 'Normalizes backslash separators before matching' {
        Mock Get-ChangedFilesFromGit { @('training\rl\uv.lock') }

        $result = @(Select-ChangedProject -Project @('training/rl', 'evaluation'))

        $result | Should -Contain 'training/rl'
    }

    It 'Forwards the base branch and lock/manifest filters to git' {
        Mock Get-ChangedFilesFromGit { @() }

        Select-ChangedProject -Project @('training/rl') -Base 'origin/develop' | Out-Null

        Should -Invoke Get-ChangedFilesFromGit -ParameterFilter {
            $BaseBranch -eq 'origin/develop' -and
            $FileExtensions -contains '*uv.lock' -and
            $FileExtensions -contains '*pyproject.toml'
        }
    }
}

Describe 'Invoke-UvLockConsistencyCheckCore' -Tag 'Unit' {
    BeforeAll { Save-CIEnvironment }
    AfterAll { Restore-CIEnvironment }

    BeforeEach {
        $script:OutputPath = Join-Path $TestDrive 'logs/uv-lock-consistency-results.json'
        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Write-CIAnnotation {}
        Mock Write-CIStepSummary {}
        Mock Get-Command { return @{ Source = '/usr/bin/uv' } } -ParameterFilter { $Name -eq 'uv' }
    }

    AfterEach {
        Restore-CIEnvironment
    }

    It 'Returns 1 when uv is not on PATH' {
        Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'uv' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl')

        $result | Should -Be 1
    }

    It 'Writes an error annotation when uv is missing' {
        Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'uv' }

        Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl') | Out-Null

        Should -Invoke Write-CIAnnotation -ParameterFilter {
            $Level -eq 'Error' -and $Message -like '*uv not found*'
        }
    }

    It 'Returns 0 when all supplied projects are consistent' {
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $true; Detail = '' }
        }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl', 'evaluation')

        $result | Should -Be 0
    }

    It 'Returns 1 and annotates when a project drifts' {
        Mock Test-UvLockProject {
            $passed = $Project -ne 'evaluation'
            [pscustomobject]@{ Project = $Project; Passed = $passed; Detail = if ($passed) { '' } else { 'drift' } }
        }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl', 'evaluation')

        $result | Should -Be 1
        Should -Invoke Write-CIAnnotation -ParameterFilter {
            $Level -eq 'Error' -and $File -eq 'evaluation/uv.lock'
        }
    }

    It 'Writes the JSON results file' {
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $true; Detail = '' }
        }

        Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl') | Out-Null

        $script:OutputPath | Should -Exist
    }

    It 'Skips checks and returns 0 when ChangedFilesOnly finds no changes' {
        Mock Select-ChangedProject { @() }
        Mock Test-UvLockProject { throw 'should not run' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl') -ChangedFilesOnly

        $result | Should -Be 0
        Should -Not -Invoke Test-UvLockProject
    }

    It 'Annotates the root uv.lock path when the root project drifts' {
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $false; Detail = 'drift' }
        }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('.')

        $result | Should -Be 1
        Should -Invoke Write-CIAnnotation -ParameterFilter {
            $Level -eq 'Error' -and $File -eq 'uv.lock'
        }
    }

    It 'Auto-discovers projects when none are supplied' {
        Mock Get-UvLockProject { @('training/rl') }
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $true; Detail = '' }
        }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 0
        Should -Invoke Get-UvLockProject -Times 1
    }

    It 'Returns 0 and skips checks when discovery finds no projects' {
        Mock Get-UvLockProject { @() }
        Mock Test-UvLockProject { throw 'should not run' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 0
        Should -Not -Invoke Test-UvLockProject
        $script:OutputPath | Should -Exist
    }

    It 'Derives the output path under the repository root when none is supplied' {
        Mock Get-UvLockProject { @('training/rl') }
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $true; Detail = '' }
        }

        Invoke-UvLockConsistencyCheckCore | Out-Null

        (Join-Path $TestDrive 'logs/uv-lock-consistency-results.json') | Should -Exist
    }

    It 'Falls back to a derived repo root when git rev-parse yields nothing' {
        Mock git { return $null } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Get-UvLockProject { @() }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 0
        Should -Invoke Get-UvLockProject -Times 1
    }
}
