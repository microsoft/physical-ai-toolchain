#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../ci/Close-ReleaseMilestone.ps1
    . $PSScriptRoot/../../ci/Install-Gitsign.ps1
    . $PSScriptRoot/../../ci/New-SignedReleaseTag.ps1
    . $PSScriptRoot/../../ci/Update-ChangelogMsDate.ps1

    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force

    function gh { }
    function git { }
}

Describe 'Close-ReleaseMilestoneCore' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
    }

    AfterEach {
        Remove-MockCIFiles -MockFiles $script:MockFiles
        Restore-CIEnvironment
    }

    It 'Closes the matching open milestone' {
        $script:PatchedMilestone = $false
        Mock gh {
            if ($args[0] -eq 'api' -and $args[1] -eq '--paginate') {
                $global:LASTEXITCODE = 0
                return '{"title":"v1.0.0","number":7,"state":"open"}'
            }
            if ($args[0] -eq 'api' -and $args[1] -eq 'repos/owner/repo/milestones/7' -and $args -notcontains '--method') {
                $global:LASTEXITCODE = 0
                return '{"open_issues":0}'
            }
            if ($args[0] -eq 'api' -and $args -contains '--method') {
                $global:LASTEXITCODE = 0
                $script:PatchedMilestone = $true
                return '{}'
            }
        }

        $result = @(Close-ReleaseMilestoneCore -TagName 'v1.0.0' -Repository 'owner/repo')

        $result[-1] | Should -Be 0
        $result | Should -Contain 'Closed milestone v1.0.0 (#7)'
        $script:PatchedMilestone | Should -BeTrue
    }

    It 'Treats a missing milestone as a non-blocking release warning' {
        Mock gh {
            $global:LASTEXITCODE = 0
            return '{"title":"v2.0.0","number":9,"state":"open"}'
        }

        $result = @(Close-ReleaseMilestoneCore -TagName 'v1.0.0' -Repository 'owner/repo')

        $result[-1] | Should -Be 0
        Get-Content $env:GITHUB_STEP_SUMMARY -ErrorAction SilentlyContinue | Should -BeNullOrEmpty
    }

    It 'Skips an already closed milestone' {
        Mock gh {
            $global:LASTEXITCODE = 0
            return '{"title":"v1.0.0","number":7,"state":"closed"}'
        }

        $result = @(Close-ReleaseMilestoneCore -TagName 'v1.0.0' -Repository 'owner/repo')

        $result[-1] | Should -Be 0
        $result | Should -Contain 'Milestone v1.0.0 (#7) already closed'
    }

    It 'Treats milestone API failures as non-blocking release warnings' {
        Mock gh {
            $global:LASTEXITCODE = 1
            return 'api failed'
        }

        Close-ReleaseMilestoneCore -TagName 'v1.0.0' -Repository 'owner/repo' | Should -Be 0
    }

    It 'Warns on open issues and keeps patch failures non-blocking' {
        Mock gh {
            if ($args[0] -eq 'api' -and $args[1] -eq '--paginate') {
                $global:LASTEXITCODE = 0
                return '{"title":"v1.0.0","number":7,"state":"open"}'
            }
            if ($args[0] -eq 'api' -and $args[1] -eq 'repos/owner/repo/milestones/7' -and $args -notcontains '--method') {
                $global:LASTEXITCODE = 0
                return '{"open_issues":2}'
            }
            if ($args[0] -eq 'api' -and $args -contains '--method') {
                $global:LASTEXITCODE = 1
                return 'patch failed'
            }
        }

        Close-ReleaseMilestoneCore -TagName 'v1.0.0' -Repository 'owner/repo' | Should -Be 0
    }

    It 'Requires a tag and repository' {
        { Close-ReleaseMilestoneCore -TagName '' -Repository 'owner/repo' } | Should -Throw -ExpectedMessage 'TAG_NAME is required.'
        { Close-ReleaseMilestoneCore -TagName 'v1.0.0' -Repository '' } | Should -Throw -ExpectedMessage 'GITHUB_REPOSITORY is required.'
    }
}

Describe 'Install-GitsignCore' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $env:GITHUB_PATH = Join-Path $TestDrive 'github-path'
    }

    AfterEach {
        Remove-MockCIFiles -MockFiles $script:MockFiles
        Restore-CIEnvironment
    }

    It 'Installs the pinned X64 gitsign asset' {
        Mock Invoke-WebRequest {
            @'
#!/usr/bin/env bash
echo gitsign version test
'@ | Set-Content -Path $OutFile -NoNewline
        }
        Mock Get-FileHash {
            [pscustomobject]@{ Hash = '011af11d57ad205b4ae4e3f41ecc8c64fbe0043c829190c0cb44fce6fce74bbb' }
        }

        $runnerTemp = Join-Path $TestDrive 'runner'
        New-Item -ItemType Directory -Force -Path $runnerTemp | Out-Null
        $result = @(Install-GitsignCore -Version 'v0.13.0' -RunnerArch 'X64' -RunnerTemp $runnerTemp)

        $result[-1] | Should -Be 0
        Test-Path (Join-Path $runnerTemp 'bin/gitsign') | Should -BeTrue
        Get-Content $env:GITHUB_PATH | Should -Contain (Join-Path $runnerTemp 'bin')
    }

    It 'Rejects unsupported runner architecture' {
        { Install-GitsignCore -RunnerArch 'S390X' -RunnerTemp $TestDrive } | Should -Throw -ExpectedMessage 'Unsupported runner architecture: S390X'
    }

    It 'Rejects missing runner inputs' {
        { Install-GitsignCore -RunnerArch '' -RunnerTemp $TestDrive } | Should -Throw -ExpectedMessage 'RUNNER_ARCH is required.'
        { Install-GitsignCore -RunnerArch 'X64' -RunnerTemp '' } | Should -Throw -ExpectedMessage 'RUNNER_TEMP is required.'
    }

    It 'Rejects checksum mismatches' {
        Mock Invoke-WebRequest {
            'not gitsign' | Set-Content -Path $OutFile
        }
        Mock Get-FileHash {
            [pscustomobject]@{ Hash = 'bad' }
        }

        $runnerTemp = Join-Path $TestDrive 'runner'
        New-Item -ItemType Directory -Force -Path $runnerTemp | Out-Null
        { Install-GitsignCore -Version 'v0.13.0' -RunnerArch 'X64' -RunnerTemp $runnerTemp } |
            Should -Throw -ExpectedMessage 'Checksum verification failed*'
    }
}

Describe 'New-SignedReleaseTagCore' -Tag 'Unit' {
    BeforeEach {
        $script:GitCalls = @()
        Mock git {
            $script:GitCalls += ($args -join ' ')
            if ($args[0] -eq 'tag' -and $args[1] -eq '--list') {
                return ''
            }
        }
    }

    It 'Creates and pushes a signed release tag when absent' {
        $result = @(New-SignedReleaseTagCore -TagName 'v1.0.0' -GitHubToken 'token' -Repository 'owner/repo' -CommitSha 'abc123')

        $result[-1] | Should -Be 0
        $script:GitCalls | Should -Contain 'tag -s v1.0.0 abc123 -m Release v1.0.0'
        $script:GitCalls | Should -Contain 'push origin refs/tags/v1.0.0'
    }

    It 'Skips creation when the tag already exists' {
        Mock git {
            $script:GitCalls += ($args -join ' ')
            if ($args[0] -eq 'tag' -and $args[1] -eq '--list') {
                return 'v1.0.0'
            }
        }

        $result = @(New-SignedReleaseTagCore -TagName 'v1.0.0' -GitHubToken 'token' -Repository 'owner/repo' -CommitSha 'abc123')

        $result[-1] | Should -Be 0
        $script:GitCalls | Should -Not -Contain 'push origin refs/tags/v1.0.0'
    }

    It 'Requires release tag inputs' {
        { New-SignedReleaseTagCore -TagName '' -GitHubToken 'token' -Repository 'owner/repo' -CommitSha 'abc123' } |
            Should -Throw -ExpectedMessage 'TAG_NAME is required.'
        { New-SignedReleaseTagCore -TagName 'v1.0.0' -GitHubToken '' -Repository 'owner/repo' -CommitSha 'abc123' } |
            Should -Throw -ExpectedMessage 'GH_TOKEN is required.'
        { New-SignedReleaseTagCore -TagName 'v1.0.0' -GitHubToken 'token' -Repository '' -CommitSha 'abc123' } |
            Should -Throw -ExpectedMessage 'GITHUB_REPOSITORY is required.'
        { New-SignedReleaseTagCore -TagName 'v1.0.0' -GitHubToken 'token' -Repository 'owner/repo' -CommitSha '' } |
            Should -Throw -ExpectedMessage 'GITHUB_SHA is required.'
    }
}

Describe 'Update-ChangelogMsDateCore' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:MockFiles = Initialize-MockCIEnvironment -Workspace $TestDrive
        $env:GITHUB_REPOSITORY = 'owner/repo'
        $script:GitCalls = @()
        Mock git {
            $script:GitCalls += ($args -join ' ')
        }
    }

    AfterEach {
        Remove-MockCIFiles -MockFiles $script:MockFiles
        Restore-CIEnvironment
    }

    It 'Updates ms.date and pushes the changelog change' {
        $changelog = Join-Path $TestDrive 'CHANGELOG.md'
        "ms.date: 2026-06-01`n# Changelog" | Set-Content -Path $changelog

        $result = @(Update-ChangelogMsDateCore -Path $changelog -Today '2026-07-01' -GitHubToken 'token')

        $result[-1] | Should -Be 0
        Get-Content $changelog -Raw | Should -Match 'ms\.date: 2026-07-01'
        $script:GitCalls | Should -Contain 'commit -m chore: stamp ms.date in CHANGELOG.md'
        $script:GitCalls | Should -Contain 'push'
    }

    It 'Does not require a token when ms.date is already current' {
        $changelog = Join-Path $TestDrive 'CHANGELOG.md'
        "ms.date: 2026-07-01`n# Changelog" | Set-Content -Path $changelog

        $result = @(Update-ChangelogMsDateCore -Path $changelog -Today '2026-07-01' -GitHubToken '')

        $result[-1] | Should -Be 0
        $script:GitCalls | Should -BeNullOrEmpty
    }

    It 'Requires a token and repository when an update must be pushed' {
        $changelog = Join-Path $TestDrive 'CHANGELOG.md'
        "ms.date: 2026-06-01`n# Changelog" | Set-Content -Path $changelog

        { Update-ChangelogMsDateCore -Path $changelog -Today '2026-07-01' -GitHubToken '' } |
            Should -Throw -ExpectedMessage 'GH_TOKEN is required to push the changelog update.'

        Remove-Item env:GITHUB_REPOSITORY -ErrorAction SilentlyContinue
        { Update-ChangelogMsDateCore -Path $changelog -Today '2026-07-01' -GitHubToken 'token' } |
            Should -Throw -ExpectedMessage 'GITHUB_REPOSITORY is required to push the changelog update.'
    }
}
