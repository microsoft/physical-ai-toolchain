#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../security/Test-HveCoreFreshness.ps1

    $script:SetupPath = Join-Path $TestDrive 'copilot-setup-steps.yml'
    @'
      - name: Bootstrap hve-core RPI persona
        env:
          # microsoft/hve-core release: hve-core-v3.2.2 (2026-03-23)
          UPSTREAM_REF: e69486a5f809ede45c63c0a31358c12912bd5168
        run: echo bootstrap
'@ | Set-Content -Path $script:SetupPath -Encoding utf8
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

    It 'Throws when the file is absent at the baseline ref' {
        Mock gh {
            if ("$args" -match 'ref=PIN') { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
            else { $global:LASTEXITCODE = 0; 'bbbbbbb' }
        }

        { Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST' } |
            Should -Throw '*Baseline file not found upstream*'
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
}

Describe 'Get-HveCoreFileDriftForBaseline' -Tag 'Unit' {
    It 'Uses the source-header SHA and resolved main SHA' {
        $path = 'scripts/security/Test-DangerousWorkflow.ps1'
        $sourceSha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $mainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'source-header' }
        Mock Get-HveCoreFileSource { [pscustomobject]@{ Path = $path; Sha = $sourceSha } }
        Mock Assert-HveCoreCommitOnMain {}
        Mock Get-HveCoreFileDrift { [pscustomobject]@{ State = 'current'; Drift = $false } }

        $null = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha $mainSha

        Should -Invoke Assert-HveCoreCommitOnMain -Times 1 -ParameterFilter {
            $CommitSha -eq $sourceSha -and $MainSha -eq $mainSha
        }
        Should -Invoke Get-HveCoreFileDrift -Times 1 -ParameterFilter {
            $Path -eq $path -and $PinnedRef -eq $sourceSha -and $LatestRef -eq $mainSha
        }
    }

    It 'Uses the release pin and latest release tag' {
        $path = 'scripts/security/Modules/SecurityHelpers.psm1'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'release' }
        Mock Get-HveCoreFileDrift { [pscustomobject]@{ State = 'current'; Drift = $false } }

        $null = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseTag 'tag' -LatestMainSha 'main'

        Should -Invoke Get-HveCoreFileDrift -Times 1 -ParameterFilter {
            $Path -eq $path -and $PinnedRef -eq 'pin' -and $LatestRef -eq 'tag'
        }
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
        $summary | Should -Match '\| Derived File \| Upstream comparison \| Pinned blob \| Latest blob \| Status \|'
        $summary | Should -Match '\| 1111111 \| 2222222 \|'
        $summary | Should -Match '\[hve-core-v1 → main\]\(https://github\.com/microsoft/hve-core/compare/hve-core-v1\.\.\.main\)'
        $summary | Should -Match 'Source-header target: b{40}'
    }
}
