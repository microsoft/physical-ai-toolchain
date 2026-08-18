#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../security/Test-HveCoreFreshness.ps1

    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:SetupPath = Join-Path $TestDrive 'copilot-setup-steps.yml'
    @'
      - name: Bootstrap hve-core RPI persona
        env:
          # microsoft/hve-core release: hve-core-v3.2.2 (2026-03-23)
          UPSTREAM_REF: e69486a5f809ede45c63c0a31358c12912bd5168
        run: echo bootstrap
'@ | Set-Content -Path $script:SetupPath -Encoding utf8
}

Describe 'DerivedFiles configuration' -Tag 'Unit' {
    It 'Tracks both security linters with source-header baselines' {
        $sourceFiles = @($script:DerivedFiles | Where-Object { $_.Baseline -eq 'source-header' })

        $sourceFiles.Count | Should -Be 2
        $sourceFiles.Path | Should -Contain 'scripts/security/Test-WorkflowPermissions.ps1'
        $sourceFiles.Path | Should -Contain 'scripts/security/Test-DangerousWorkflow.ps1'
    }

    It 'Parses the provenance header of every source-header entry' {
        $sourceFiles = @($script:DerivedFiles | Where-Object { $_.Baseline -eq 'source-header' })

        foreach ($file in $sourceFiles) {
            $source = Get-HveCoreFileSource -Path (Join-Path $script:RepoRoot $file.Path)
            $source.Path | Should -Be $file.Path
            $source.Sha | Should -Match '^[0-9a-f]{40}$'
        }
    }
}

Describe 'Get-PinnedHveCoreRef' -Tag 'Unit' {
    It 'Extracts the pinned SHA and release tag' {
        $ref = Get-PinnedHveCoreRef -Path $script:SetupPath
        $ref.Sha | Should -Be 'e69486a5f809ede45c63c0a31358c12912bd5168'
        $ref.Tag | Should -Be 'hve-core-v3.2.2'
    }

    It 'Returns null for a missing file' {
        Get-PinnedHveCoreRef -Path (Join-Path $TestDrive 'missing.yml') | Should -BeNullOrEmpty
    }

    It 'Returns a null Sha when UPSTREAM_REF is absent' {
        $p = Join-Path $TestDrive 'no-ref.yml'
        "env:`n  FOO: bar" | Set-Content -Path $p -Encoding utf8
        $ref = Get-PinnedHveCoreRef -Path $p
        $ref.Sha | Should -BeNullOrEmpty
        $ref.Tag | Should -Be 'unknown'
    }
}

Describe 'Select-LatestRelease' -Tag 'Unit' {
    It 'Picks the newest non-draft release by created_at' {
        $releases = @(
            [pscustomobject]@{ tag_name = 'old'; draft = $false; created_at = '2026-01-01T00:00:00Z'; html_url = 'u-old' }
            [pscustomobject]@{ tag_name = 'newest'; draft = $false; created_at = '2026-03-01T00:00:00Z'; html_url = 'u-new' }
            [pscustomobject]@{ tag_name = 'mid'; draft = $false; created_at = '2026-02-01T00:00:00Z'; html_url = 'u-mid' }
        )
        (Select-LatestRelease -Releases $releases).tag_name | Should -Be 'newest'
    }

    It 'Skips drafts even when a draft is newer' {
        $releases = @(
            [pscustomobject]@{ tag_name = 'stable'; draft = $false; created_at = '2026-02-01T00:00:00Z'; html_url = 'u-stable' }
            [pscustomobject]@{ tag_name = 'draft'; draft = $true; created_at = '2026-03-01T00:00:00Z'; html_url = 'u-draft' }
        )
        (Select-LatestRelease -Releases $releases).tag_name | Should -Be 'stable'
    }

    It 'Returns null when there are no non-draft releases' {
        $releases = @(
            [pscustomobject]@{ tag_name = 'draft'; draft = $true; created_at = '2026-03-01T00:00:00Z'; html_url = 'u-draft' }
        )
        Select-LatestRelease -Releases $releases | Should -BeNullOrEmpty
    }

    It 'Returns null for an empty collection' {
        Select-LatestRelease -Releases @() | Should -BeNullOrEmpty
    }
}

Describe 'Get-HveCoreFileSource' -Tag 'Unit' {
    It 'Extracts the source path and normalizes the commit SHA' {
        $path = Join-Path $TestDrive 'derived.ps1'
        @'
Adapted from microsoft/hve-core scripts/security/Test-DangerousWorkflow.ps1
as of commit ABCDEF1234567890ABCDEF1234567890ABCDEF12.
'@ | Set-Content -Path $path -Encoding utf8

        $source = Get-HveCoreFileSource -Path $path

        $source.Path | Should -Be 'scripts/security/Test-DangerousWorkflow.ps1'
        $source.Sha | Should -Be 'abcdef1234567890abcdef1234567890abcdef12'
    }

    It 'Accepts a source path outside scripts/security' {
        $path = Join-Path $TestDrive 'derived.psm1'
        @'
Adapted from microsoft/hve-core scripts/linting/Modules/Example.psm1
as of commit ABCDEF1234567890ABCDEF1234567890ABCDEF12.
'@ | Set-Content -Path $path -Encoding utf8

        (Get-HveCoreFileSource -Path $path).Path | Should -Be 'scripts/linting/Modules/Example.psm1'
    }

    It 'Throws when multiple source headers are present' {
        $path = Join-Path $TestDrive 'duplicate-header.ps1'
        @'
Adapted from microsoft/hve-core scripts/security/First.ps1 as of commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.
Adapted from microsoft/hve-core scripts/security/Second.ps1 as of commit bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.
'@ | Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*multiple*'
    }

    It 'Throws when the source header is missing' {
        $path = Join-Path $TestDrive 'missing-header.ps1'
        '# no provenance header' | Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*Could not extract*'
    }

    It 'Throws when the file does not exist' {
        { Get-HveCoreFileSource -Path (Join-Path $TestDrive 'missing.ps1') } | Should -Throw '*not found locally*'
    }
}

Describe 'Get-DriftState' -Tag 'Unit' {
    It 'Returns current when pinned and latest upstream SHAs match' {
        Get-DriftState -PinnedUpstreamSha 'abc123' -LatestUpstreamSha 'abc123' | Should -Be 'current'
    }

    It 'Returns current when SHAs match case-insensitively' {
        Get-DriftState -PinnedUpstreamSha 'ABC123' -LatestUpstreamSha 'abc123' | Should -Be 'current'
    }

    It 'Returns drift when SHAs differ' {
        Get-DriftState -PinnedUpstreamSha 'abc123' -LatestUpstreamSha 'def456' | Should -Be 'drift'
    }

    It 'Returns missing-upstream when the latest upstream SHA is empty' {
        Get-DriftState -PinnedUpstreamSha 'abc123' -LatestUpstreamSha '' | Should -Be 'missing-upstream'
    }

    It 'Returns missing-baseline when the pinned upstream SHA is empty' {
        Get-DriftState -PinnedUpstreamSha '' -LatestUpstreamSha 'abc123' | Should -Be 'missing-baseline'
    }
}

Describe 'Get-HveCoreBlobSha' -Tag 'Unit' {
    It 'Returns the trimmed blob SHA on success' {
        Mock gh { $global:LASTEXITCODE = 0; 'abc123def456' }
        Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' | Should -Be 'abc123def456'
    }

    It 'Returns empty string on a genuine 404 (file absent at ref)' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
        Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' | Should -Be ''
    }

    It 'Throws on a non-404 gh api failure (transient/auth/rate limit)' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Internal Server Error (HTTP 500)' }
        { Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' } | Should -Throw
    }
}

Describe 'Resolve-HveCoreCommitSha' -Tag 'Unit' {
    It 'Returns the trimmed commit SHA when the ref resolves' {
        Mock gh { $global:LASTEXITCODE = 0; 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef' }
        Resolve-HveCoreCommitSha -Repo 'o/r' -Ref 'v1' | Should -Be 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    }

    It 'Throws when the ref does not resolve (invalid tag or pinned SHA)' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
        { Resolve-HveCoreCommitSha -Repo 'o/r' -Ref 'definitely-not-a-ref' } | Should -Throw
    }
}

Describe 'Get-HveCoreFileDrift' -Tag 'Unit' {
    It 'Reports drift when the upstream blob changed between refs' {
        Mock gh {
            $global:LASTEXITCODE = 0
            if ("$args" -match 'ref=PIN') { 'aaaaaaa' } else { 'bbbbbbb' }
        }
        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST'
        $r.State | Should -Be 'drift'
        $r.Drift | Should -BeTrue
        $r.PinnedUpstreamSha | Should -Be 'aaaaaaa'
        $r.LatestUpstreamSha | Should -Be 'bbbbbbb'
        $r.PinnedRef | Should -Be 'PIN'
        $r.LatestRef | Should -Be 'LATEST'
        $r.ComparisonUrl | Should -Be 'https://github.com/o/r/compare/PIN...LATEST'
    }

    It 'Reports current when the upstream blob is unchanged' {
        Mock gh { $global:LASTEXITCODE = 0; 'samesha' }
        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST'
        $r.State | Should -Be 'current'
        $r.Drift | Should -BeFalse
    }

    It 'Reports missing-upstream when the file is absent at the latest ref' {
        Mock gh {
            if ("$args" -match 'ref=LATEST') { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
            else { $global:LASTEXITCODE = 0; 'aaaaaaa' }
        }
        (Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST').State | Should -Be 'missing-upstream'
    }

    It 'Reports missing-baseline when the file is absent at the baseline ref' {
        Mock gh {
            if ("$args" -match 'ref=PIN') { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
            else { $global:LASTEXITCODE = 0; 'bbbbbbb' }
        }

        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST'
        $r.State | Should -Be 'missing-baseline'
        $r.Drift | Should -BeTrue
    }

    It 'URL-encodes refs in comparison links' {
        Mock gh { $global:LASTEXITCODE = 0; 'samesha' }

        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'v1](https://evil.example)' -LatestRef 'main'

        $r.ComparisonUrl | Should -Not -Match '\]\('
        $r.ComparisonUrl | Should -Match 'v1%5D%28https%3A%2F%2Fevil\.example%29'
    }
}

Describe 'Assert-HveCoreCommitOnMain' -Tag 'Unit' {
    It 'Accepts a commit whose merge base with main is itself' {
        Mock gh { $global:LASTEXITCODE = 0; 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }

        {
            Assert-HveCoreCommitOnMain -Repo 'o/r' `
                -CommitSha 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
                -MainSha 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        } | Should -Not -Throw
    }

    It 'Throws when the recorded commit is not an ancestor of main' {
        Mock gh { $global:LASTEXITCODE = 0; 'cccccccccccccccccccccccccccccccccccccccc' }

        {
            Assert-HveCoreCommitOnMain -Repo 'o/r' `
                -CommitSha 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
                -MainSha 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        } | Should -Throw '*not an ancestor*'
    }

    It 'Throws when the compare API fails' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }

        {
            Assert-HveCoreCommitOnMain -Repo 'o/r' `
                -CommitSha 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
                -MainSha 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        } | Should -Throw '*Could not verify source commit*'
    }

    It 'Throws when the compare API returns no merge base' {
        Mock gh { $global:LASTEXITCODE = 0; '' }

        {
            Assert-HveCoreCommitOnMain -Repo 'o/r' `
                -CommitSha 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' `
                -MainSha 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        } | Should -Throw '*Could not verify source commit*'
    }
}

Describe 'Get-HveCoreFileDriftForBaseline' -Tag 'Unit' {
    BeforeEach {
        Mock Test-Path { $true }
    }

    It 'Uses the source-header SHA and resolved main SHA' {
        $path = 'scripts/security/Test-DangerousWorkflow.ps1'
        $sourceSha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $mainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'source-header' }
        Mock Get-HveCoreFileSource { [pscustomobject]@{ Path = $path; Sha = $sourceSha } }
        Mock Assert-HveCoreCommitOnMain {}
        Mock Get-HveCoreFileDrift { [pscustomobject]@{ State = 'current'; Drift = $false } }

        $null = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha $mainSha

        Should -Invoke Assert-HveCoreCommitOnMain -Times 1 -Exactly -ParameterFilter {
            $CommitSha -eq $sourceSha -and $MainSha -eq $mainSha
        }
        Should -Invoke Get-HveCoreFileDrift -Times 1 -Exactly -ParameterFilter {
            $Path -eq $path -and $PinnedRef -eq $sourceSha -and $LatestRef -eq $mainSha
        }
    }

    It 'Uses the release pin and latest release tag' {
        $path = 'scripts/security/Modules/SecurityHelpers.psm1'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'release' }
        Mock Get-HveCoreFileSource { throw 'source parser must not run' }
        Mock Assert-HveCoreCommitOnMain { throw 'ancestry check must not run' }
        Mock Get-HveCoreFileDrift { [pscustomobject]@{ State = 'current'; Drift = $false } }

        $null = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha 'main'

        Should -Invoke Get-HveCoreFileDrift -Times 1 -Exactly -ParameterFilter {
            $Path -eq $path -and $PinnedRef -eq 'pin' -and $LatestRef -eq 'tag'
        }
        Should -Invoke Get-HveCoreFileSource -Times 0 -Exactly
        Should -Invoke Assert-HveCoreCommitOnMain -Times 0 -Exactly
    }

    It 'Throws when the recorded upstream path differs from the local path' {
        $file = [pscustomobject]@{
            Path = 'scripts/security/Test-DangerousWorkflow.ps1'
            Baseline = 'source-header'
        }
        Mock Get-HveCoreFileSource {
            [pscustomobject]@{
                Path = 'scripts/security/Other.ps1'
                Sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            }
        }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha 'main'
        } | Should -Throw '*must match its recorded*'
    }

    It 'Throws on an unsupported baseline' {
        $file = [pscustomobject]@{ Path = 'scripts/security/Test-DangerousWorkflow.ps1'; Baseline = 'bogus' }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha 'main'
        } | Should -Throw '*Unsupported hve-core baseline*'
    }

    It 'Throws when the derived file is missing locally' {
        Mock Test-Path { $false }
        $file = [pscustomobject]@{ Path = 'scripts/security/Missing.ps1'; Baseline = 'release' }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha 'main'
        } | Should -Throw '*not found locally*'
    }
}

Describe 'Format-HveCoreDriftCells' -Tag 'Unit' {
    It 'Renders missing-upstream records with short and empty SHAs' {
        $file = [pscustomobject]@{
            Baseline = 'source-header'
            PinnedUpstreamSha = 'abc'
            LatestUpstreamSha = ''
            PinnedRef = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
            LatestRef = 'main'
            State = 'missing-upstream'
            ComparisonUrl = ''
        }

        $cells = Format-HveCoreDriftCells -File $file

        $cells.Baseline | Should -Be 'Source header'
        $cells.PinnedSha | Should -Be 'abc'
        $cells.LatestSha | Should -BeExactly ''
        $cells.Comparison | Should -BeExactly ''
        $cells.Status | Should -Match 'Not found at target ref'
    }

    It 'Escapes error details for markdown output' {
        $file = [pscustomobject]@{
            Baseline = 'release'
            PinnedUpstreamSha = ''
            LatestUpstreamSha = ''
            PinnedRef = ''
            LatestRef = ''
            State = 'error'
            Error = "bad | value`nnext"
            ComparisonUrl = ''
        }

        (Format-HveCoreDriftCells -File $file).Status | Should -Be '❌ Check failed — <code>bad &#124; value next</code>'
    }

    It 'Escapes ref text in comparison links' {
        $file = [pscustomobject]@{
            Baseline = 'release'
            PinnedUpstreamSha = 'abc123'
            LatestUpstreamSha = 'def456'
            PinnedRef = 'v1](https://evil.example)'
            LatestRef = 'main'
            State = 'drift'
            ComparisonUrl = 'https://github.com/o/r/compare/v1%5D%28https%3A%2F%2Fevil.example%29...main'
        }

        $comparison = (Format-HveCoreDriftCells -File $file).Comparison

        $comparison | Should -Not -Match '\]\(https://evil'
        $comparison | Should -Match 'v1&#93;&#40;https://evil\.example&#41;'
    }
}

Describe 'Format-HveCoreIssueBody' -Tag 'Unit' {
    It 'Formats the issue body with correct markers and links' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'http://u'
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path = 'scripts/x.psm1'
                    Baseline = 'release'
                    PinnedUpstreamSha = '1111111'
                    LatestUpstreamSha = '2222222'
                    PinnedRef = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                    LatestRef = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                    ComparisonUrl = 'https://github.com/microsoft/hve-core/compare/a...b'
                    Drift = $true
                    State = 'drift'
                }
            )
        }
        $body = Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'

        $body | Should -Match '<!-- automation:hve-core-freshness -->'
        $body | Should -Match 'hve-core-v9'
        $body | Should -Match 'scripts/x\.psm1'
        $body | Should -Match 'compare/hve-core-v1\.\.\.hve-core-v9'
        $body | Should -Match '\[aaaaaaa → bbbbbbb\]\(https://github\.com/microsoft/hve-core/compare/a\.\.\.b\)'
        $body | Should -Not -Match '[Pp]ersona'
    }

    It 'Falls back to the pinned SHA in the compare link when the tag is unknown' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'http://u'
            Pin = [pscustomobject]@{
                PinnedTag = 'unknown'
                PinnedSha = 'abcdef1234567890'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path = 'scripts/x.psm1'
                    Baseline = 'source-header'
                    PinnedUpstreamSha = '1111111'
                    LatestUpstreamSha = '2222222'
                    PinnedRef = 'hve-core-v1'
                    LatestRef = 'main'
                    ComparisonUrl = 'https://github.com/microsoft/hve-core/compare/hve-core-v1...main'
                    Drift = $true
                    State = 'drift'
                }
            )
        }
        $body = Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'

        $body | Should -Match 'compare/abcdef1234567890\.\.\.hve-core-v9'
        $body | Should -Not -Match 'compare/unknown'
    }
}

Describe 'Format-HveCoreJobSummary' -Tag 'Unit' {
    It 'Formats the job summary correctly' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'http://u'
            LatestMainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path = 'scripts/x.psm1'
                    Baseline = 'source-header'
                    PinnedUpstreamSha = '1111111'
                    LatestUpstreamSha = '2222222'
                    PinnedRef = 'hve-core-v1'
                    LatestRef = 'main'
                    ComparisonUrl = 'https://github.com/microsoft/hve-core/compare/hve-core-v1...main'
                    Drift = $true
                    State = 'drift'
                }
            )
        }
        $summary = Format-HveCoreJobSummary -Result $r

        $summary | Should -Match 'hve-core-v9'
        $summary | Should -Match 'scripts/x\.psm1'
        $summary | Should -Match '⚠️ Upstream advanced'
        $summary | Should -Match '\| Derived File \| Baseline \| Upstream comparison \| Baseline blob \| Target blob \| Status \|'
        $summary | Should -Match '\| Source header \|'
        $summary | Should -Match '\| 1111111 \| 2222222 \|'
        $summary | Should -Match '\[hve-core-v1 → main\]\(https://github\.com/microsoft/hve-core/compare/hve-core-v1\.\.\.main\)'
        $summary | Should -Match 'Source-header target: b{40}'
    }
}

Describe 'Invoke-HveCoreFreshnessCheck' -Tag 'Unit' {
    BeforeEach {
        Mock Get-HveCoreReleases {
            @([pscustomobject]@{
                    tag_name = 'hve-core-v9'
                    draft = $false
                    created_at = '2026-08-01T00:00:00Z'
                    html_url = 'https://example.test/release'
                })
        }
        Mock Resolve-HveCoreCommitSha {
            if ($Ref -eq 'main') { 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }
            else { 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        }
        Mock Get-PinnedHveCoreRef {
            [ordered]@{
                Tag = 'hve-core-v1'
                Sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            }
        }
        Mock Get-HveCoreFileDriftForBaseline {
            [ordered]@{
                Path = $File.Path
                PinnedUpstreamSha = '1111111'
                LatestUpstreamSha = '1111111'
                PinnedRef = 'pin'
                LatestRef = 'target'
                ComparisonUrl = 'https://example.test/compare'
                Drift = $false
                State = 'current'
            }
        }
    }

    It 'Writes the resolved main SHA and all configured files to results JSON' {
        $resultsFile = Join-Path $TestDrive 'results.json'

        $outcome = Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        $result = Get-Content -Path $resultsFile -Raw | ConvertFrom-Json

        $outcome.StaleCount | Should -Be 0
        $result.LatestMainSha | Should -Be 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        @($result.Files).Count | Should -Be $script:DerivedFiles.Count
        @($result.Files | Where-Object { $_.Baseline -eq 'source-header' }).Count | Should -Be 2
    }

    It 'Records a file-level error and continues checking remaining files' {
        $resultsFile = Join-Path $TestDrive 'results-with-error.json'
        Mock Get-HveCoreFileDriftForBaseline {
            if ($File.Path -eq 'scripts/security/Test-DangerousWorkflow.ps1') {
                throw 'invalid provenance header'
            }
            [ordered]@{
                Path = $File.Path
                PinnedUpstreamSha = '1111111'
                LatestUpstreamSha = '1111111'
                PinnedRef = 'pin'
                LatestRef = 'target'
                ComparisonUrl = 'https://example.test/compare'
                Drift = $false
                State = 'current'
            }
        }

        $outcome = Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        $result = Get-Content -Path $resultsFile -Raw | ConvertFrom-Json
        $failed = $result.Files | Where-Object { $_.Path -eq 'scripts/security/Test-DangerousWorkflow.ps1' }

        $outcome.StaleCount | Should -Be 1
        @($result.Files).Count | Should -Be $script:DerivedFiles.Count
        $failed.State | Should -Be 'error'
        $failed.Error | Should -Be 'invalid provenance header'
        $failed.Drift | Should -BeTrue
    }
}
