#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../ci/Add-ReleaseVerificationNotes.ps1
    . $PSScriptRoot/../../ci/Close-ReleaseMilestone.ps1
    . $PSScriptRoot/../../ci/Install-Gitsign.ps1
    . $PSScriptRoot/../../ci/New-SignedReleaseTag.ps1
    . $PSScriptRoot/../../ci/New-SigningArtifacts.ps1
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

Describe 'New-SigningArtifactsCore' -Tag 'Unit' {
    BeforeEach {
        $script:SourceJson = '{"mediaType":"x","dsseEnvelope":{"payload":"cGF5","payloadType":"application/vnd.in-toto+json","signatures":[{"sig":"c2ln","keyid":""}]}}'
        $script:WheelJson = '{"mediaType":"y","dsseEnvelope":{"payload":"d2hlZWw=","payloadType":"application/vnd.in-toto+json","signatures":[{"sig":"d3NpZw==","keyid":""}]}}'
        $script:SourceBundle = Join-Path $TestDrive 'source.sigstore.json'
        $script:WheelBundle = Join-Path $TestDrive 'wheel.sigstore.json'
        Set-Content -LiteralPath $script:SourceBundle -Value $script:SourceJson -NoNewline -Encoding utf8NoBOM
        Set-Content -LiteralPath $script:WheelBundle -Value $script:WheelJson -NoNewline -Encoding utf8NoBOM
    }

    It 'Copies the bundles and writes compact dsseEnvelope intoto lines' {
        Push-Location $TestDrive
        try {
            $result = @(New-SigningArtifactsCore -BundlePath $script:SourceBundle -WheelBundlePath $script:WheelBundle -Tag 'v1.2.3')
        }
        finally {
            Pop-Location
        }

        $result[-1] | Should -Be 0

        Get-Content -LiteralPath (Join-Path $TestDrive 'source-v1.2.3.sigstore.json') -Raw | Should -Be $script:SourceJson
        Get-Content -LiteralPath (Join-Path $TestDrive 'wheels-v1.2.3.sigstore.json') -Raw | Should -Be $script:WheelJson

        Get-Content -LiteralPath (Join-Path $TestDrive 'source-v1.2.3.intoto.jsonl') -Raw |
            Should -Be ('{"payload":"cGF5","payloadType":"application/vnd.in-toto+json","signatures":[{"sig":"c2ln","keyid":""}]}' + "`n")
        Get-Content -LiteralPath (Join-Path $TestDrive 'wheels-v1.2.3.intoto.jsonl') -Raw |
            Should -Be ('{"payload":"d2hlZWw=","payloadType":"application/vnd.in-toto+json","signatures":[{"sig":"d3NpZw==","keyid":""}]}' + "`n")
    }

    It 'Requires both bundle paths and the tag' {
        { New-SigningArtifactsCore -BundlePath '' -WheelBundlePath 'w' -Tag 'v1.0.0' } |
            Should -Throw -ExpectedMessage 'BUNDLE_PATH is required.'
        { New-SigningArtifactsCore -BundlePath 'b' -WheelBundlePath '' -Tag 'v1.0.0' } |
            Should -Throw -ExpectedMessage 'WHEEL_BUNDLE_PATH is required.'
        { New-SigningArtifactsCore -BundlePath 'b' -WheelBundlePath 'w' -Tag '' } |
            Should -Throw -ExpectedMessage 'TAG is required.'
    }
}

Describe 'Add-ReleaseVerificationNotesCore' -Tag 'Unit' {
    It 'Prepends the current body and substitutes the tag into the verification section' {
        $script:EditArgs = $null
        Mock gh {
            if ($args -contains 'view') {
                $global:LASTEXITCODE = 0
                return 'Release body line one', '', 'Second paragraph'
            }
            if ($args -contains 'edit') {
                $global:LASTEXITCODE = 0
                $script:EditArgs = $args -join ' '
                return ''
            }
        }

        Push-Location $TestDrive
        try {
            $result = @(Add-ReleaseVerificationNotesCore -Tag 'v9.9.9' -Repository 'owner/repo')
        }
        finally {
            Pop-Location
        }

        $result[-1] | Should -Be 0
        $notes = Get-Content -LiteralPath (Join-Path $TestDrive 'notes.md') -Raw
        $expectedVerification = @'

---

## Artifact Verification

All release artifacts include [Sigstore](https://www.sigstore.dev/) provenance attestations. Verify with the [GitHub CLI](https://cli.github.com/):

```bash
# Download the source archive
gh release download v9.9.9 --repo microsoft/physical-ai-toolchain --pattern 'source-v9.9.9.tar.gz'

# Verify build provenance
gh attestation verify source-v9.9.9.tar.gz --repo microsoft/physical-ai-toolchain

# Verify SBOM attestation
gh attestation verify source-v9.9.9.tar.gz --repo microsoft/physical-ai-toolchain --predicate-type https://spdx.dev/Document

# Download and verify the signed wheel (identity ends @refs/heads/main: the pipeline runs on push to main)
gh release download v9.9.9 --repo microsoft/physical-ai-toolchain --pattern '*.whl' --pattern '*.whl.sigstore.json'
sigstore verify identity *.whl --bundle *.whl.sigstore.json --cert-identity 'https://github.com/microsoft/physical-ai-toolchain/.github/workflows/main.yml@refs/heads/main' --cert-oidc-issuer 'https://token.actions.githubusercontent.com'

# Download the CycloneDX SBOMs (SPDX equivalents also attached)
gh release download v9.9.9 --repo microsoft/physical-ai-toolchain --pattern 'sbom.cdx.json' --pattern 'dependencies.cdx.json'
```
'@
        $expected = "Release body line one`n`nSecond paragraph`n" + $expectedVerification + "`n"
        $notes | Should -Be $expected
        $script:EditArgs | Should -Be 'release edit v9.9.9 --repo owner/repo --notes-file notes.md'
    }

    It 'Strips trailing newlines from the release body to match the original bash behavior' {
        Mock gh {
            if ($args -contains 'view') {
                $global:LASTEXITCODE = 0
                return 'Body line one', '', 'Body line two', '', ''
            }
            if ($args -contains 'edit') {
                $global:LASTEXITCODE = 0
                return ''
            }
        }

        Push-Location $TestDrive
        try {
            $result = @(Add-ReleaseVerificationNotesCore -Tag 'v9.9.9' -Repository 'owner/repo')
        }
        finally {
            Pop-Location
        }

        $result[-1] | Should -Be 0
        $notes = Get-Content -LiteralPath (Join-Path $TestDrive 'notes.md') -Raw
        $notes.StartsWith("Body line one`n`nBody line two`n`n---") | Should -BeTrue
        $notes | Should -Not -Match "Body line two`n`n`n---"
    }

    It 'Requires a tag and repository' {
        { Add-ReleaseVerificationNotesCore -Tag '' -Repository 'owner/repo' } |
            Should -Throw -ExpectedMessage 'TAG is required.'
        { Add-ReleaseVerificationNotesCore -Tag 'v1.0.0' -Repository '' } |
            Should -Throw -ExpectedMessage 'GITHUB_REPOSITORY is required.'
    }

    It 'Fails when the release body cannot be read' {
        Mock gh {
            $global:LASTEXITCODE = 1
            return 'not found'
        }

        { Add-ReleaseVerificationNotesCore -Tag 'v1.0.0' -Repository 'owner/repo' } |
            Should -Throw -ExpectedMessage 'Failed to read release notes for v1.0.0.'
    }

    It 'Fails when the release edit does not succeed' {
        Mock gh {
            if ($args -contains 'view') {
                $global:LASTEXITCODE = 0
                return 'body'
            }
            if ($args -contains 'edit') {
                $global:LASTEXITCODE = 1
                return 'edit failed'
            }
        }

        Push-Location $TestDrive
        try {
            { Add-ReleaseVerificationNotesCore -Tag 'v1.0.0' -Repository 'owner/repo' } |
                Should -Throw -ExpectedMessage 'Failed to update release notes for v1.0.0.'
        }
        finally {
            Pop-Location
        }
    }
}
