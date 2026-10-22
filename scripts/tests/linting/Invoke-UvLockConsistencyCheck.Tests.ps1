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

Describe 'Get-UvProject' -Tag 'Unit' {
    BeforeEach {
        Mock git {
            $global:LASTEXITCODE = 0
            $root = $args[1]
            Get-ChildItem -Path $root -Filter pyproject.toml -Recurse -File | ForEach-Object {
                [IO.Path]::GetRelativePath($root, $_.FullName) -replace '\\', '/'
            }
        } -ParameterFilter { $args[0] -eq '-C' }
    }

    It 'Discovers tracked manifest directories as repo-relative paths' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'training/rl') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'evaluation') | Out-Null
        '[project]' | Set-Content (Join-Path $TestDrive 'pyproject.toml')
        '[project]' | Set-Content (Join-Path $TestDrive 'training/rl/pyproject.toml')
        '[project]' | Set-Content (Join-Path $TestDrive 'evaluation/pyproject.toml')

        $projects = Get-UvProject -RepoRoot $TestDrive

        $projects | Should -Contain '.'
        $projects | Should -Contain 'training/rl'
        $projects | Should -Contain 'evaluation'
        $projects.Count | Should -Be 3
    }

    It 'Excludes vendored and tooling directories' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'external/IsaacLab') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive '.venv') | Out-Null
        '[project]' | Set-Content (Join-Path $TestDrive 'external/IsaacLab/pyproject.toml')
        '[project]' | Set-Content (Join-Path $TestDrive '.venv/pyproject.toml')

        $projects = Get-UvProject -RepoRoot $TestDrive

        $projects | Should -Not -Contain 'external/IsaacLab'
        $projects | Should -Not -Contain '.venv'
    }

    It 'Returns an empty array when no tracked manifests exist' {
        $emptyRoot = Join-Path $TestDrive 'empty'
        New-Item -ItemType Directory -Force -Path $emptyRoot | Out-Null

        $projects = @(Get-UvProject -RepoRoot $emptyRoot)

        $projects.Count | Should -Be 0
    }

    It 'Does not shrink the expected inventory when one project loses its lock' {
        foreach ($project in @('intact', 'missing-lock')) {
            New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive $project) | Out-Null
            '[project]' | Set-Content (Join-Path $TestDrive "$project/pyproject.toml")
        }
        'lock' | Set-Content (Join-Path $TestDrive 'intact/uv.lock')
        $projects = @(Get-UvProject -RepoRoot $TestDrive)
        $projects | Should -Contain 'intact'
        $projects | Should -Contain 'missing-lock'
    }

    It 'Fails manifest discovery when Git fails' {
        Mock git { $global:LASTEXITCODE = 1 } -ParameterFilter { $args[0] -eq '-C' }
        { Get-UvProject -RepoRoot $TestDrive } | Should -Throw '*tracked project manifests*'
    }
}

Describe 'Get-UvProject workspace ownership' -Tag 'Unit' {
    BeforeEach {
        Remove-Item (Join-Path $TestDrive 'data/library/uv.lock') -ErrorAction SilentlyContinue
        foreach ($project in @('data/library', 'evaluation', 'evaluation/judge')) {
            New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive $project) | Out-Null
            @"
[project]
name = "$($project.Replace('/', '-'))"
version = "0.1.0"
"@ | Set-Content (Join-Path $TestDrive "$project/pyproject.toml")
        }
        @'
[project]
name = "workspace-owner"
version = "0.1.0"
[tool.uv.workspace]
members = ["data/library", "evaluation/judge"]
'@ | Set-Content (Join-Path $TestDrive 'pyproject.toml')
        'lock' | Set-Content (Join-Path $TestDrive 'uv.lock')
        'lock' | Set-Content (Join-Path $TestDrive 'evaluation/uv.lock')
        Mock git {
            $global:LASTEXITCODE = 0
            $root = $args[1]
            $filter = if ($args -contains 'pyproject.toml') { 'pyproject.toml' } else { 'uv.lock' }
            Get-ChildItem -Path $root -Filter $filter -Recurse -File | ForEach-Object {
                [IO.Path]::GetRelativePath($root, $_.FullName) -replace '\\', '/'
            }
        } -ParameterFilter { $args[0] -eq '-C' }
    }

    It 'Checks the explicit workspace owner once without requiring member locks' {
        $projects = @(Get-UvProject -RepoRoot $TestDrive)

        $projects | Should -Be @('.', 'evaluation')
    }

    It 'Keeps the independent parent of an explicitly selected nested member' {
        $projects = @(Get-UvProject -RepoRoot $TestDrive)

        $projects | Should -Contain 'evaluation'
        $projects | Should -Not -Contain 'evaluation/judge'
    }

    It 'Does not implicitly absorb a nested nonmember' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'data/independent') | Out-Null
        '[project]' | Set-Content (Join-Path $TestDrive 'data/independent/pyproject.toml')

        @(Get-UvProject -RepoRoot $TestDrive) | Should -Contain 'data/independent'
    }

    It 'Rejects a missing explicitly declared workspace member' {
        Remove-Item (Join-Path $TestDrive 'data/library/pyproject.toml')

        { Get-UvProject -RepoRoot $TestDrive } | Should -Throw '*member*'
    }

    It 'Rejects an orphan lock under a workspace member' {
        'lock' | Set-Content (Join-Path $TestDrive 'data/library/uv.lock')

        { Get-UvProject -RepoRoot $TestDrive } | Should -Throw '*lock*'
    }

    It 'Rejects malformed workspace membership instead of treating members as standalone' {
        @'
[project]
name = "workspace-owner"
version = "0.1.0"
[tool.uv.workspace]
members = "data/library"
'@ | Set-Content (Join-Path $TestDrive 'pyproject.toml')

        { Get-UvProject -RepoRoot $TestDrive } | Should -Throw '*workspace*'
    }

    It 'Selects the root lock for a member-only manifest change' {
        Mock Get-ChangedFilesFromGit { @('evaluation/judge/pyproject.toml') }

        @(Select-ChangedProject -Project @('.', 'evaluation') -RepoRoot $TestDrive) | Should -Be @('.')
    }

    It 'Keeps independent parent changes separate from the nested workspace member' {
        Mock Get-ChangedFilesFromGit { @('evaluation/pyproject.toml') }

        @(Select-ChangedProject -Project @('.', 'evaluation') -RepoRoot $TestDrive) | Should -Be @('evaluation')
    }
}

Describe 'Get-CommittedUvLockProject' -Tag 'Unit' {
    It 'Omits deleted tracked locks without hiding existing standalone locks' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'independent') | Out-Null
        'lock' | Set-Content (Join-Path $TestDrive 'independent/uv.lock')
        Mock git {
            $global:LASTEXITCODE = 0
            @('deleted/uv.lock', 'independent/uv.lock')
        }

        @(Get-CommittedUvLockProject -RepoRoot $TestDrive) | Should -Be @('independent')
    }
}

Describe 'Get-UvDependabotProject' -Tag 'Unit' {
    It 'Reads and normalizes uv directories' {
        $dependabotPath = Join-Path $TestDrive 'dependabot.yml'
        @'
version: 2
updates:
- package-ecosystem: uv
  directory: /
- package-ecosystem: npm
  directory: /
- package-ecosystem: "uv"
  directory: "/training/rl"
'@ | Set-Content -Path $dependabotPath

        $projects = @(Get-UvDependabotProject -Path $dependabotPath)

        $projects | Should -Be @('.', 'training/rl')
    }
}

Describe 'Get-MissingUvDependabotProject' -Tag 'Unit' {
    It 'Returns lock projects without uv update ownership' {
        $missing = @(
            Get-MissingUvDependabotProject `
                -LockProjects @('.', 'evaluation', 'evaluation/vlm_judge') `
                -DependabotProjects @('.', 'evaluation')
        )

        $missing | Should -Be @('evaluation/vlm_judge')
    }
}

Describe 'Invoke-UvLockCheck' -Tag 'Unit' {
    It 'Regenerates one authoritative workspace lock for <Scenario>' -ForEach @(
        @{ Scenario = 'leaf-patch'; Repeated = $false; Optional = $false },
        @{ Scenario = 'repeated-exact-pins'; Repeated = $true; Optional = $false },
        @{ Scenario = 'inactive-extra-floor'; Repeated = $true; Optional = $true }
    ) {
        $uvExecutable = (Get-Command uv -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $workspace = Join-Path $TestDrive $Scenario
        foreach ($directory in @($workspace, "$workspace/library", "$workspace/wheels")) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
        $lockArgs = @('--offline', '--no-index', '--find-links', "$workspace/wheels")
        foreach ($version in @('0.1.0', '0.1.1')) {
            $distribution = "probe_leaf-$version.dist-info"
            $archive = [IO.Compression.ZipFile]::Open("$workspace/wheels/probe_leaf-$version-py3-none-any.whl", 'Create')
            try {
                $entries = @{
                    "$distribution/METADATA" = "Metadata-Version: 2.3`nName: probe-leaf`nVersion: $version`n"
                    "$distribution/WHEEL" = "Wheel-Version: 1.0`nRoot-Is-Purelib: true`nTag: py3-none-any`n"
                    "$distribution/RECORD" = ''
                }
                foreach ($entry in $entries.GetEnumerator()) {
                    $writer = [IO.StreamWriter]::new($archive.CreateEntry($entry.Key).Open())
                    try { $writer.Write($entry.Value) }
                    finally { $writer.Dispose() }
                }
            }
            finally { $archive.Dispose() }
        }
        $ownerManifest = @'
[project]
name = "update-probe"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["probe-leaf==0.1.0", "probe-library==0.1.0"]
[tool.uv]
package = false
[tool.uv.workspace]
members = ["library"]
[tool.uv.sources]
probe-library = { workspace = true }
'@
        $libraryManifest = @'
[project]
name = "probe-library"
version = "0.1.0"
requires-python = ">=3.12"
'@
        if ($Repeated) {
            $libraryManifest += if ($Optional) {
                "`n[project.optional-dependencies]`nunused = [`"probe-leaf==0.1.0`"]`n"
            }
            else {
                "`ndependencies = [`"probe-leaf==0.1.0`"]`n"
            }
        }
        $ownerManifest | Set-Content "$workspace/pyproject.toml"
        $libraryManifest | Set-Content "$workspace/library/pyproject.toml"
            & $uvExecutable --directory $workspace lock @lockArgs 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0
        $originalLock = Get-Content "$workspace/uv.lock" -Raw

        $ownerManifest.Replace('probe-leaf==0.1.0', 'probe-leaf==0.1.1') | Set-Content "$workspace/pyproject.toml"
        & $uvExecutable --directory $workspace lock --check @lockArgs 2>&1 | Out-Null
        $LASTEXITCODE | Should -Not -Be 0
        (Get-Content "$workspace/uv.lock" -Raw) | Should -BeExactly $originalLock
        if ($Repeated) {
            & $uvExecutable --directory $workspace lock @lockArgs 2>&1 | Out-Null
            $LASTEXITCODE | Should -Not -Be 0
            $libraryManifest.Replace('probe-leaf==0.1.0', 'probe-leaf==0.1.1') | Set-Content "$workspace/library/pyproject.toml"
        }
        & $uvExecutable --directory $workspace lock @lockArgs 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0
        & $uvExecutable --directory $workspace lock --check @lockArgs 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0
        (Get-Content "$workspace/uv.lock" -Raw) | Should -Not -BeExactly $originalLock
        "$workspace/library/uv.lock" | Should -Not -Exist
        "$workspace/leaf/uv.lock" | Should -Not -Exist
    }

    It 'Detects changed inactive extra metadata without a resolved optional package' {
        $uvExecutable = (Get-Command uv -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $consumer = Join-Path $TestDrive 'metadata-consumer'
        foreach ($directory in @($consumer, "$consumer/library", "$consumer/feature")) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
        @'
[project]
name = "metadata-consumer"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["metadata-library==0.1.0"]
[tool.uv]
package = false
[tool.uv.sources]
metadata-library = { path = "library" }
metadata-feature = { path = "feature" }
'@ | Set-Content (Join-Path $consumer 'pyproject.toml')
        $libraryManifest = @'
[project]
name = "metadata-library"
version = "0.1.0"
requires-python = ">=3.12"
[project.optional-dependencies]
parquet = ["metadata-feature==0.1.0"]
[tool.uv.sources]
metadata-feature = { path = "../feature" }
'@
        $libraryManifest | Set-Content (Join-Path $consumer 'library/pyproject.toml')
        @'
[project]
name = "metadata-feature"
version = "0.1.0"
requires-python = ">=3.12"
'@ | Set-Content (Join-Path $consumer 'feature/pyproject.toml')

        & $uvExecutable --directory $consumer lock --offline 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0
        $lockBefore = Get-Content (Join-Path $consumer 'uv.lock') -Raw
        $lockBefore | Should -Not -Match '(?m)^name = "metadata-feature"$'
        & $uvExecutable --directory $consumer lock --check --offline 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 0

        $libraryManifest.Replace('parquet =', 'changed-parquet =') |
            Set-Content (Join-Path $consumer 'library/pyproject.toml')
        & $uvExecutable --directory $consumer lock --check --offline 2>&1 | Out-Null

        $LASTEXITCODE | Should -Not -Be 0
        (Get-Content (Join-Path $consumer 'uv.lock') -Raw) | Should -BeExactly $lockBefore
    }

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
    BeforeEach {
        foreach ($project in @('.', 'training/rl', 'evaluation')) {
            $path = Join-Path $TestDrive $project
            New-Item -ItemType Directory -Force -Path $path | Out-Null
            'lock' | Set-Content (Join-Path $path 'uv.lock')
            '[project]' | Set-Content (Join-Path $path 'pyproject.toml')
        }
        Mock git { $global:LASTEXITCODE = 0; $args[-1] } -ParameterFilter { $args -contains 'ls-files' }
    }

    It 'Rejects a missing lock before invoking uv' {
        Remove-Item (Join-Path $TestDrive 'evaluation/uv.lock')
        Mock Invoke-UvLockCheck { throw 'must not execute' }
        $result = Test-UvLockProject -Project 'evaluation' -RepoRoot $TestDrive
        $result.Passed | Should -BeFalse
        $result.Detail | Should -Be 'uv.lock is missing'
        Should -Not -Invoke Invoke-UvLockCheck
    }

    It 'Rejects an untracked lock even when it exists on disk' {
        Mock git { $global:LASTEXITCODE = 1 } -ParameterFilter { $args -contains 'ls-files' }
        (Test-UvLockProject -Project 'evaluation' -RepoRoot $TestDrive).Passed | Should -BeFalse
    }

    It 'Rejects an explicitly selected project without a manifest' {
        Remove-Item (Join-Path $TestDrive 'evaluation/pyproject.toml')
        Mock Invoke-UvLockCheck { throw 'should not run' }

        $result = Test-UvLockProject -Project 'evaluation' -RepoRoot $TestDrive

        $result.Passed | Should -BeFalse
        $result.Detail | Should -Be 'pyproject.toml is missing'
        Should -Not -Invoke Invoke-UvLockCheck
    }

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

    It 'Reports an unavailable interpreter as a failed check, not lock drift' {
        $results = @([pscustomobject]@{
                Project = '.'
                Passed = $false
                Detail = 'No interpreter found for Python 3.12.13'
            })

        $report = New-UvLockReport -Results $results -OutputPath $script:OutputPath

        $summary = Get-Content $report.MarkdownPath -Raw
        $summary | Should -Match 'Failed Checks'
        $summary | Should -Match 'No interpreter found for Python 3.12.13'
        $summary | Should -Not -Match 'Run `uv lock`'
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
        Mock Get-CommittedUvLockProject { @('training/rl') }
        Mock Get-UvDependabotProject { @('training/rl') }
    }

    AfterEach {
        Restore-CIEnvironment
    }

    It 'Returns 1 when uv is not on PATH' {
        Mock Get-Command { return $null } -ParameterFilter { $Name -eq 'uv' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl')

        $result | Should -Be 1
    }

    It 'Returns 1 when a committed uv project lacks Dependabot ownership' {
        Mock Get-CommittedUvLockProject { @('training/rl', 'evaluation/vlm_judge') }
        Mock Get-UvDependabotProject { @('training/rl') }
        Mock Test-UvLockProject { throw 'should not run' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath -Projects @('training/rl')

        $result | Should -Be 1
        Should -Invoke Write-CIAnnotation -ParameterFilter {
            $Level -eq 'Error' -and
            $File -eq '.github/dependabot.yml' -and
            $Message -like "*evaluation/vlm_judge*"
        }
        Should -Not -Invoke Test-UvLockProject
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

    It 'Fails a discovered manifest without a checked-in lock' {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestDrive 'evaluation') | Out-Null
        'manifest' | Set-Content (Join-Path $TestDrive 'evaluation/pyproject.toml')
        Mock Get-UvProject { @('evaluation') }
        Mock Invoke-UvLockCheck { throw 'should not run' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 1
        (Get-Content $script:OutputPath -Raw | ConvertFrom-Json).results[0].Detail | Should -Be 'uv.lock is missing'
        Should -Invoke Write-CIAnnotation -ParameterFilter { $File -eq 'evaluation/uv.lock' }
        Should -Not -Invoke Invoke-UvLockCheck
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
        Mock Get-UvProject { @('training/rl') }
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $true; Detail = '' }
        }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 0
        Should -Invoke Get-UvProject -Times 1
    }

    It 'Fails full validation when discovery finds no projects' {
        Mock Get-UvProject { @() }
        Mock Test-UvLockProject { throw 'should not run' }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 1
        Should -Not -Invoke Test-UvLockProject
        Should -Invoke Write-CIAnnotation -ParameterFilter { $Level -eq 'Error' -and $Message -like '*no tracked project manifests*' }
    }

    It 'Derives the output path under the repository root when none is supplied' {
        Mock Get-UvProject { @('training/rl') }
        Mock Test-UvLockProject {
            [pscustomobject]@{ Project = $Project; Passed = $true; Detail = '' }
        }

        Invoke-UvLockConsistencyCheckCore | Out-Null

        (Join-Path $TestDrive 'logs/uv-lock-consistency-results.json') | Should -Exist
    }

    It 'Falls back to a derived repo root when git rev-parse yields nothing' {
        Mock git { return $null } -ParameterFilter { $args[0] -eq 'rev-parse' }
        Mock Get-UvProject { @() }

        $result = Invoke-UvLockConsistencyCheckCore -OutputPath $script:OutputPath

        $result | Should -Be 1
        Should -Invoke Get-UvProject -Times 1
    }
}
